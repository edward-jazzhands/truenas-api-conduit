# standard library
import asyncio
from typing import Any
import json
import ssl
from pathlib import Path
import logging
import sys
import time
import socket
import subprocess
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

# third party
import websockets.client as client
# from websockets.client import WebSocketClientProtocol
import websockets.exceptions as ws_exceptions

# project
from truenas_api_conduit import APP_NAME
from truenas_api_conduit.core import PLATFORM, Platform
from truenas_api_conduit.config import Config

RECONNECT_DELAY: int = 10
HEARTBEAT: int = 30

log = logging.getLogger(__name__)


__all__ = [
    "TrueNASClient",
]



def simple_socket_check(address: str, port: int) -> bool:
    """Use address='8.8.8.8' and port=53 for a basic internet check."""
    log.debug("Starting simple_socket_check")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect((address, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def use_ping_tool(address: str) -> bool:
    log.debug("Starting use_ping_tool")

    if PLATFORM == Platform.WINDOWS:
        args = ["ping", "-n", "1", "-w", "1000", address]
    else:
        args = ["ping", "-c", "1", "-W", "1", address]
    
    try:
        result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False


def curl_test(address: str, api_route: str, timeout: int = 3) -> bool | None:
    log.debug("Starting curl_test")

    url = f"https://{address}{api_route}"

    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["curl", url],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        log.debug("curl is not installed or not found on PATH.")
        return None
    except subprocess.CalledProcessError as e:
        log.debug(f"curl failed (exit {e.returncode}): {e.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        log.debug(f"curl timed out after {timeout} seconds")
        return False

    if result.stdout.find("WebSocket"):
        return True
    else:
        return False

@dataclass
class ConnDiag:
    has_internet: bool
    socket_check: bool
    ping_check: bool
    curl_check: bool | None


async def run_connection_diagnostic(config: Config) -> ConnDiag:

    if config.truenas_host.find(":"):
        host, port = config.truenas_host.split(":")
    else:
        # NOTE: The address for the TrueNAS websocket API is always the
        # same as the web UI's HTTPS address, with /api/current at the end.
        host = config.truenas_host
        port = 443

    diag_tests = {
        "has_internet": partial(simple_socket_check, "8.8.8.8", 53),
        "socket_check": partial(simple_socket_check, host, int(port)),
        "ping_check": partial(use_ping_tool, host),
        "curl_check": partial(curl_test, config.truenas_host, config.api_route)
    }

    executor = ThreadPoolExecutor(max_workers=8)
    futures = {
        executor.submit(func): test_name
        for test_name, func in diag_tests.items()
    }

    # as_completed(futures) is a generator that yields each future as it
    # finishes regardless of the order they were submitted.
    diag_results = {}
    for future in as_completed(futures):
        test_name = futures[future]
        log.debug("%s finished", test_name)
        diag_results[test_name] = future.result()
    return ConnDiag(**diag_results)

async def _get_websocket_conn(cfg: Config) -> client.WebSocketClientProtocol:

    log.info("Starting _get_websocket_conn")

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  #! why TLS?

    if cfg.validate_certs and cfg.truenas_cert_path:
        cert_path_obj = Path(cfg.truenas_cert_path)
        ssl_context.load_verify_locations(cafile=cert_path_obj)
        log.info("Loaded certificate for validation")
    elif cfg.validate_certs:
        log.info(
            "Validate certs but no cert provided. The server will need to have "
            "a cert must be from a trusted CA for auth to work"
        )
        pass
    else:
        ssl_context.check_hostname = False  # must disable this first
        ssl_context.verify_mode = ssl.CERT_NONE
        log.info("Disabled certificate validation")

    diag = await run_connection_diagnostic(cfg)
    log.debug(diag)

    return await client.connect(
        cfg.uri, ssl=ssl_context, user_agent_header=APP_NAME, open_timeout=2
    )

class TrueNASClient:
    """wrapper around the websocket connection. This is created by the aiohttp web
    server and shares aiohttp's event loop."""

    def __init__(self, config: Config) -> None:
        self.config = config
        "pydantic-settings Config object"

        self.ws_conn: client.WebSocketClientProtocol
        "Websocket connection client"

        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        """Maps request IDs -> asyncio Futures waiting for responses.
        This is how HTTP requests get their matching websocket response"""

        self.req_id = 1
        self.authenticated: bool = False

    def error_handler(self, err_string: str):

        if self.config.log_level == "debug":
            log.exception(err_string)
            sys.exit(1)
        elif self.config.log_level == "trace":
            log.error(err_string)
            raise
        else:
            log.error(err_string)
            sys.exit(1)

    async def status(self) -> dict[str, Any]:

        if self.authenticated:
            start_time = time.time()
            await self({"method": "core.ping", "params": []})
            end_time = time.time()
            ping = f"{(end_time - start_time)*1000:.0f} ms"
        else:
            ping = "not authenticated"

        return {
            "client-server ping:": ping,
            "authenticated": self.authenticated,
            "req_id": self.req_id,
            "ws_conn host": self.ws_conn._host,
            "ws_conn port": self.ws_conn._port,
            "socket_port": self.config.socket_port,
            "ws_conn secure": self.ws_conn._secure,
            "truenas_cert_path": self.config.truenas_cert_path,
            "validate_certs": self.config.validate_certs,
            "api_key": str(self.config.api_key),
            "log_level": self.config.log_level,
            "no_color": self.config.no_color,
        }

    def make_rpc_request(
        self,
        method: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:

        d = {
            "id": self.req_id,
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
        }
        self.req_id += 1
        return d

    async def connect(self) -> bool:

        try:
            await self._connect()
        except ssl.SSLError as e:
            # SSL errors mean the HTTPS connection is not working, often due to
            # a bad certificate.
            err_str = (
                f"SSL error: {e}\n"
                "You can fix this by:\n"
                "  - Using a trusted certificate signed by a CA\n"
                "  - Setting truenas_cert_path to the path of your self-signed certificate\n"
                "  - Setting validate_certs to False\n"
            )
            self.error_handler(err_str)
        except (ws_exceptions.InvalidURI, socket.gaierror) as e:
            err_str = (
                f"Address resolution error: {e} {e.__class__} | Most likely cause is the "
                f"address for TRUENAS_HOST is not correct "
            )
            self.error_handler(err_str)
        except TimeoutError:
            conn_diag = await run_connection_diagnostic(self.config)
            err_string = (
                "Connection diagnostics:\n"
                f"  Outbound internet working:  {conn_diag.has_internet}\n"
                f"  TrueNAS port test:  {conn_diag.socket_check}\n"
                f"  TrueNAS ping test:  {conn_diag.ping_check}\n"
                f"  TrueNAS curl test:  {conn_diag.curl_check}\n"
            )
            if not conn_diag.socket_check:
                err_string += (
                    "\nCould not reach your TrueNAS server. The server might "
                    "be down or you may have entered the incorrect address"
                )
            if conn_diag.socket_check and conn_diag.curl_check is False:
                err_string += (
                    "\nYour TrueNAS server seems to be reachable, but the API "
                    "endpoint is not. This suggests some kind of firewall or "
                    "security issue"
                )                
            log.critical(err_string)
        except OSError as e:
            err_string = f"{getattr(e, '__module__', 'none')}.{repr(e)}"
            err_string += str(e) if str(e) else ""
            if e.strerror:
                err_string += f": {e.strerror}"
            if e.errno:
                err_string += f"  (Code: {e.errno})"
            if e.__context__:
                full_context = f"{getattr(e.__context__, '__module__', 'none')}.{repr(e.__context__)}"
                err_string += f"\n  Occurred while handling: {full_context}"
            if e.__cause__:
                full_cause = (
                    f"{getattr(e.__cause__, '__module__', 'none')}.{repr(e.__cause__)}"
                )
                err_string += f"\n  Caused by: {full_cause}"

            self.error_handler(err_string)
        else:
            return True
        return False

    async def _connect(self) -> None:

        self.ws_conn = await _get_websocket_conn(self.config)

        req = self.make_rpc_request(
            "auth.login_with_api_key", [self.config.api_key.get_secret_value()]
        )
        await self.ws_conn.send(json.dumps(req))

        # We have to read from recv manually to ensure we're authenticated
        # before starting the reader loop.

        raw = await self.ws_conn.recv()
        try:
            auth_response = json.loads(raw)
        except json.JSONDecodeError as e:
            log.error("Malformed response in auth: %s", e)
            await self.ws_conn.close()
            return

        if not auth_response.get("result"):
            log.error("Authentication failed")
            await self.ws_conn.close()
            return

        log.info("Authenticated.")
        self.authenticated = True
        asyncio.create_task(self._reader_loop())
        asyncio.create_task(self._heartbeat())

    async def close(self) -> None:
        await self.ws_conn.close()

    #! not used right now
    async def _reconnect(self) -> None:
        log.info("Reconnecting in %s seconds...", RECONNECT_DELAY)
        await asyncio.sleep(RECONNECT_DELAY)
        await self.connect()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT)
            try:
                await self.ws_conn.ping()
            except ws_exceptions.ConnectionClosed:
                log.error("Heartbeat ping failed")
                break  # reader loop will handle reconnect
            else:
                log.info("Heartbeat ping successful")

    # NOTE: Just to refresh your brain on how this works if you're rusty, in a
    # proper websocket client architecture, the sending logic and the receiving logic
    # are separated into two different concerns. The sending logic is the "writer"
    # and the receiving logic is the "reader". Technically, sending and receiving
    # are two different streams, websockets just abstract that into one interface.
    # That's why we need the pending dict. __call__ just fires off the request
    # and returns a future.

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        "takes a request dict, returns a response dict"

        if not self.authenticated:
            raise RuntimeError("Websocket client is not authenticated")

        payload["jsonrpc"] = "2.0"  #  in case its not set already
        if "id" not in payload:  #  in case client supplies id
            payload["id"] = self.req_id
            self.req_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        self.pending[payload["id"]] = future  # so the reader loop can find it

        await self.ws_conn.send(json.dumps(payload))
        return await future  #! is this supposed to await the future before returning?

    @property
    def call(self):
        return self.__call__

    async def _reader_loop(self) -> None:

        # NOTE: This comment section is copied from the websockets.connect docstring:
        # [ws] supports asynchronous iteration to receive incoming messages:
        # ```
        #     async for message in websocket:
        #         await process(message)
        # ```
        # The iterator exits normally when the connection is closed with close code
        # 1000 (OK) or 1001 (going away) or without a close code. It raises
        # a :exc:~websockets.exceptions.ConnectionClosedError when the connection
        # is closed with any other code.

        log.debug("Starting reader loop")
        try:
            async for msg in self.ws_conn:
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError as e:
                    log.error("Malformed response, skipping: %s", e)
                    continue

                try:
                    req_id = data["id"]  #    match response ID to request ID
                except KeyError:
                    log.error("Response has no ID, skipping")
                    continue

                try:
                    future = self.pending.pop(req_id)
                except KeyError:
                    log.warning(f"No pending future for request ID {req_id}, skipping")
                    continue

                log.info("Request #%s was successful", req_id)

                # The future was awaited in the __call__ method. So when we
                # set the result, this signals to the original caller (aiohttp)
                # that the call is complete.
                future.set_result(data)

        except ws_exceptions.ConnectionClosedError as e:
            log.error("Connection closed unexpectedly: %s", e)
        except ws_exceptions.WebSocketException as e:
            log.error("Websocket error: %s %s\n", e, e.__class__)
        except Exception as e:
            self.error_handler(f"Unknown reader loop error: {e}")
        finally:
            # Any futures still waiting will never get a response, fail them
            for future in self.pending.values():
                future.cancel()
            self.pending.clear()
            self.authenticated = False
