# standard library
import asyncio
from typing import Any
import json
import ssl
from pathlib import Path
import logging
import json
import sys

# third party
import websockets.client as client
import websockets.exceptions as ws_exceptions

# project
# from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.config import Config

UPTIME_OUTPUT = Path("/tmp/uptime.txt")
RECONNECT_DELAY: int = 10
HEARTBEAT: int = 30

log = logging.getLogger(__name__)


__all__ = [
    "TrueNASClient",
    "get_common_requests",
]


def get_common_requests() -> dict[str, list[Any]]:

    # Server Metrics I want to collect:

    #   - System uptime (days) - system.info
    #   - CPU usage (percent)  - reporting.get_data "{"name":"cpu"}, {"start":$start,"end":$end,"aggregate":true}"
    #   - CPU temperature (degrees C)
    #   - RAM usage (percent)
    #   - Disk usage (percent) - disk.query or possibly pool.query
    #   - Network usage (bytes/s)
    #   - Number of active alerts - alert.list

    return {
        "system.info": [],
        "pool.query": [],
    }


async def _get_websocket_conn(cfg: Config) -> client.WebSocketClientProtocol:

    log.info("Starting get_websocket_client")

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT) #! why TLS?
    
    if cfg.validate_certs and cfg.truenas_cert_path:
        cert_path_obj = Path(cfg.truenas_cert_path)
        ssl_context.load_verify_locations(cafile=cert_path_obj)
        log.debug("Loaded certificate for validation")
    elif cfg.validate_certs:
        log.debug("Validate certs but no cert provided, the cert must be from a trusted CA")
        pass
    else:
        ssl_context.check_hostname = False  # must disable this first
        ssl_context.verify_mode = ssl.CERT_NONE
        log.debug("Disabled certificate validation")

    return await client.connect(cfg.uri, ssl=ssl_context)


def _write_results(msg: dict[str, Any]) -> None:

    uptime = msg.get("result", {}).get("uptime")
    if uptime is not None:
        # Write atomically to a temp file then replace, so programs never
        # reads a half-written file mid-update
        tmp = UPTIME_OUTPUT.with_suffix(".tmp")
        tmp.write_text(str(uptime) + "\n")
        tmp.replace(UPTIME_OUTPUT)

        log.debug("Uptime: %s", uptime)
    else:
        log.error("Unexpected response: %s", msg)



class TrueNASClient:
    """wrapper around the websocket connection. This is created by the aiohttp web
    server and shares aiohttp's event loop."""

    def __init__(self, config: Config) -> None:
        self.config = config
        "pyantic-settings Config object"

        self.ws_conn: client.WebSocketClientProtocol
        "Websocket connection client"

        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        """Maps request IDs -> asyncio Futures waiting for responses.
        This is how HTTP requests get their matching websocket response"""

        self.req_id = 1
        self.authenticated: bool = False

    def status(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "req_id": self.req_id,
            "ws_conn host": self.ws_conn._host,
            "ws_conn port": self.ws_conn._port,
            "ws_conn secure": self.ws_conn._secure,
            "pending": len(self.pending),
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

    async def connect(self) -> None: 

        import socket
        try:
            await self._connect()
        except ssl.SSLError as e:
            # SSL errors mean the HTTPS connection is not working, often due to
            # a bad certificate.
            log.error(
                f"SSL error: {e}\n"
                "You can fix this by:\n"
                "  - Using a trusted certificate signed by a CA\n"
                "  - Setting truenas_cert_path to the path of your self-signed certificate\n"
                "  - Setting validate_certs to False\n"
            )
            if self.config.log_level == "trace":
                raise
            sys.exit(1)
        except (ws_exceptions.InvalidURI, socket.gaierror) as e:
            log.error(
                f"Address resolution error: {e} {e.__class__} | Most likely cause is the "
                f"address for TRUENAS_HOST is not correct "
            )
            if self.config.log_level == "trace":
                raise
            sys.exit(1)
        except OSError as e:
            log.error("OS Connection error: %s %s", e, e.__class__)
            if self.config.log_level == "trace":
                raise
            sys.exit(1)

    async def _connect(self) -> None:

        self.ws_conn = await _get_websocket_conn(self.config)

        req = self.make_rpc_request("auth.login_with_api_key", [self.config.api_key])
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
                break  # reader loop will handle reconnect
                    
    # NOTE: Just to refresh your brain on how this works if you're rusty, in a
    # proper websocket client architecture, the sending logic and the receiving logic 
    # are separated into two different concerns. The sending logic is the "writer"
    # and the receiving logic is the "reader". Technically, sending and receiving
    # are two different streams, websockets just abstracts that into one interface.
    # That's why we need the pending dict. __call__ just fires off the request
    # and returns a future.

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        "takes a request dict, returns a response dict"

        if not self.authenticated:
            raise RuntimeError("Websocket client is not authenticated")

        payload["jsonrpc"] = "2.0"  #  in case its not set already
        if "id" not in payload:     #  in case client supplies id
            payload["id"] = self.req_id
            self.req_id += 1
        
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        self.pending[payload["id"]] = future  # so the reader loop can find it

        await self.ws_conn.send(json.dumps(payload))
        return await future
                
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
            log.error("Unknown reader loop error: %s", e)
            raise
        finally:
            # Any futures still waiting will never get a response, fail them
            for future in self.pending.values():
                future.cancel()
            self.pending.clear()
            self.authenticated = False
            await self._reconnect()
