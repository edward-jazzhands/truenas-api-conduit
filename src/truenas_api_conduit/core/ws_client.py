# standard library
import asyncio
from typing import Any, Final, assert_never
import json
import ssl
from pathlib import Path
import logging
import time
import socket

# third party
import websockets.client as client
import websockets.exceptions as ws_exceptions
from aiohttp.web_runner import GracefulExit

# project
from truenas_api_conduit import APP_NAME
from truenas_api_conduit.config import Config
from truenas_api_conduit.core.conn_diag import ConnDiag, run_connection_diagnostic

OPEN_TIMEOUT: Final[int] = 5  #         when creating the websocket client
REQUEST_WAIT_TIME: Final[int] = 10  #   request comes in but server is reconnecting
STATUS_WAIT_TIME: Final[float] = 0.5  # how long to wait for a status check
HEARTBEAT: Final[int] = 30  #           from client to TrueNAS
PING_TIMEOUT: Final[int] = 10  #        how long to wait if a heartbeat fails
MAX_DELAY: Final[int] = 60  #           max wait between reconnections if connection drops

log = logging.getLogger(__name__)


__all__ = [
    "TrueNASClient",
]


class TrueNASClient:
    """wrapper around the websocket connection. This is created by the aiohttp web
    server and shares aiohttp's event loop. It will fail to load if there is
    no running event loop"""

    def __init__(self, config: Config) -> None:
        self.config = config
        "pydantic-settings Config object"

        self.ws_conn: client.WebSocketClientProtocol | None = None
        "Websocket connection client"

        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        """Maps request IDs -> asyncio Futures waiting for responses.
        This is how HTTP requests get their matching websocket response"""

        self.req_id = 1
        "increments one with each request"

        self.is_connected = asyncio.Event()
        "Signals when the websocket is ready to send data."

        self._reconnect_lock = asyncio.Lock()
        "Ensures only one reconnect loop runs at a time."

    def error_handler(self, err_string: str):

        if self.config.log_level == "debug":
            log.exception(err_string)
            raise GracefulExit()
        elif self.config.log_level == "trace":
            log.error(err_string)
            raise
        else:
            log.error(err_string)
            raise GracefulExit()

    async def status(self) -> dict[str, Any]:

        if not self.ws_conn:
            client_status = "Client failed to start"
        else:
            client_status = f"{repr(self.ws_conn)} started"

        result = False
        try:
            result = await asyncio.wait_for(
                self.is_connected.wait(), timeout=STATUS_WAIT_TIME
            )
        except TimeoutError:
            log.debug(
                "TrueNAS API status request timed out waiting for websocket reconnection."
            )

        if result:
            start_time = time.time()
            await self({"method": "core.ping", "params": []})
            end_time = time.time()
            ping = f"{(end_time - start_time)*1000:.0f} ms"
        else:
            ping = "not authenticated"
            log.warning("Request received but client is not connected")

        return {
            "client-status": client_status,
            "connected": result,
            "client-server ping": ping,
            "req_id": self.req_id,
            "websocket host": self.ws_conn._host if self.ws_conn else None,
            "websocket port": self.ws_conn._port if self.ws_conn else None,
            "socket_port": self.config.socket_port,
            "websocket secure": self.ws_conn._secure if self.ws_conn else None,
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

    async def _get_websocket_conn(self, cfg: Config) -> client.WebSocketClientProtocol:

        log.info("Starting _get_websocket_conn")

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # required for wss

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

        results = {}
        async for test_name, result in run_connection_diagnostic(cfg):
            results[test_name] = result
            log.info("%s result: %s", test_name, result)

        self.conn_diag = ConnDiag(**results)
        log.debug(self.conn_diag)

        return await client.connect(
            cfg.uri,
            ssl=ssl_context,
            user_agent_header=APP_NAME,
            open_timeout=OPEN_TIMEOUT,
            ping_interval=HEARTBEAT,
            ping_timeout=PING_TIMEOUT,
        )

    async def connect(self) -> bool:
        "instead of calling this directly, consider using the connection_loop method"

        try:
            await self._connect()
        except TimeoutError:
            # NOTE: Timeout is the only error for which this will simply raise,
            # which raises the error to the connection_loop method and allows it
            # to retry in a loop.
            # The rest of the exceptions are considered unrecoverable and will
            # cause the program to shut down.
            err_string = str(self.conn_diag)
            if not self.conn_diag.socket_check:
                err_string += (
                    "\nCould not reach your TrueNAS server. The server might "
                    "be down or you may have entered the incorrect address"
                )
            if self.conn_diag.socket_check and self.conn_diag.curl_check is False:
                err_string += (
                    "\nYour TrueNAS server seems to be reachable, but the API "
                    "endpoint is not. This suggests some kind of firewall or "
                    "security issue"
                )
            log.critical(err_string)
            raise
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
        assert_never(True)  # I love this function

    async def _connect(self) -> None:

        self.ws_conn = await self._get_websocket_conn(self.config)

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
        self.is_connected.set()
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def close(self) -> None:
        if self.ws_conn:
            await self.ws_conn.close()

    def reconnect(self):

        self.is_connected.clear()
        err = ConnectionError("Websocket connection dropped unexpectedly")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(err)  # Fail any pending futures
        self.pending.clear()

        asyncio.create_task(self.connection_loop())

    async def connection_loop(self) -> bool:
        """False means something else has the reconnect lock. True
        means it worked. Otherwise loops forver."""

        if self._reconnect_lock.locked():  # only one at a time
            return False

        async with self._reconnect_lock:
            log.info("Initiating websocket connection sequence...")

            # NOTE: This will just keep retrying forever. That's intentional,
            # since this is a background service that is expected behavior and
            # good user UX.

            delay = 2
            while not self.is_connected.is_set():
                try:
                    await self.connect()
                    log.info("Successfully reconnected to TrueNAS!")
                    break
                except Exception as e:
                    log.error("Reconnect attempt failed. Retrying in %ss...", delay)
                    await asyncio.sleep(delay)
                    # Exponential backoff
                    delay = min(delay * 2, MAX_DELAY)

        return True

    # NOTE: Just to refresh your brain on how this works if you're rusty, in a
    # proper websocket client architecture, the sending logic and the receiving logic
    # are separated into two different concerns. The sending logic is the "writer"
    # and the receiving logic is the "reader". Technically, sending and receiving
    # are two different streams, websockets just abstract that into one interface.
    # That's why we need the pending dict. __call__ just fires off the request
    # and returns a future.

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:

        if not self.ws_conn:
            log.warning("Request received but client is not connected")
            return {"result": "NOT CONNECTED"}

        # check connection is alive before makig request
        try:
            # The request wait time adds a buffer in case its in the middle of
            # trying to reconnect
            await asyncio.wait_for(self.is_connected.wait(), timeout=REQUEST_WAIT_TIME)
        except TimeoutError:
            raise TimeoutError(
                "TrueNAS API request timed out waiting for websocket reconnection."
            )

        payload["jsonrpc"] = "2.0"
        if "id" not in payload:
            payload["id"] = self.req_id
            self.req_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        self.pending[payload["id"]] = future

        try:
            await self.ws_conn.send(json.dumps(payload))
        except Exception:
            # Clean up the pending dict if the send fails instantly, otherwise
            # the request will be stuck in memory
            self.pending.pop(payload["id"], None)
            raise

        return await future

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

        if not self.ws_conn:
            log.warning("Tried to start reader loop but websocket client was not created")
            return

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
        finally:
            self.reconnect()
