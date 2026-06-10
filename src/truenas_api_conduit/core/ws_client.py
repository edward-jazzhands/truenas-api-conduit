# standard library
import asyncio
from typing import Any, Final
import json
import ssl
from pathlib import Path
import logging
import time
import socket
from contextlib import suppress

# third party
import websockets.client as client
import websockets.exceptions as ws_exceptions

# project
from truenas_api_conduit import APP_NAME
from truenas_api_conduit.config import Config
from truenas_api_conduit.core.conn_diag import ConnDiag, run_connection_diagnostic

OPEN_TIMEOUT: Final[int] = 5  #         when creating the websocket client
REQUEST_WAIT_TIME: Final[int] = 10  #   request comes in but server is reconnecting
RESPONSE_TIMEOUT: Final[int] = 10  #    how long to wait for response in a call
HEARTBEAT: Final[int] = 30  #           from client to TrueNAS
PING_TIMEOUT: Final[int] = 10  #        how long to wait if a heartbeat fails
MAX_RECONNECT_WAIT: Final[int] = 60  #  max wait between reconnections if connection drops

log = logging.getLogger(__name__)


__all__ = [
    "TrueNASClient",
]


class TrueNASClient:
    """wrapper around the websocket connection. This is created by the aiohttp web
    server, and shares aiohttp's event loop (must be passed in)"""

    def __init__(self, config: Config, loop: asyncio.AbstractEventLoop) -> None:

        # NOTE: My own code has no reason to pass in the loop but its an easy thing
        # to future proof so why tf not

        self.config = config
        "pydantic-settings Config object"

        self.loop = loop
        "the asyncio event loop"

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

        self.client_task: asyncio.Task | None = None

        self._closing = False

        self.conn_diag: ConnDiag | None = None

    def start(self) -> asyncio.Task:
        "returns the created task"
        # NOTE: We need to store a reference of the task as a self attribute
        # so that it does not get garbage collected by python
        self.client_task = asyncio.create_task(self._start())
        return self.client_task

    async def _start(self) -> None:
        "runs for the lifetime of the app."

        while not self._closing:
            try:
                # this will loop indefinitely until it connects, with an
                # exponential backoff
                await self._connection_loop()

                # this will loop indefinitely until the connection drops.
                # it doesn't block the event loop because the ws_conn
                # provides an async generator that runs forever and wakes up
                # when messages come in on the read pipe
                await self._reader_loop()

            # If this cancel is because a shutdown was requested from close(),
            # then self._closing will be set to True. So when we break the loop,
            # the function will complete and the task should get marked as finished.
            # If for some reason this was not caused by the close() command, then
            # the while loop will restart
            except asyncio.CancelledError:
                log.warning("Websocket client received a cancel command")
                break
            except Exception as e:
                log.error(f"Worker caught error: {e}")
                # This class should swallow all errors unless we're on trace.
                # The websocket connection errors are irrelevant to the web server
                # that created this class.
                if self.config.log_level == "trace":
                    raise
            finally:
                # if we got here then the reader loop was broken
                self._cleanup_pending()

    async def close(self) -> None:

        if not self.client_task:
            raise RuntimeError("Tried to close, but there's no client task")

        self._closing = True

        # recall that cancel does not actually turn anything off immediately,
        # it just gets asyncio to raise a CancelledError inside of the running
        # task, which will be raised by asyncio at the very next thing in
        # the task that tries to await something (its asyncio magic, dont ask
        # too many questions, you just have to believe)
        self.client_task.cancel()

        # That CancelledError will get caught in the reader loop and cause the
        # task to shut down. Then we can safely close the websocket connection
        # without affecting an active reader
        if self.ws_conn:
            await self.ws_conn.close(reason="Server shutting down")

        # Last we have to await the task to block here until it finishes.
        # This is necessary to give the CancelledError time to bubble up through
        # the task and allow the client to gracefully close itself, so that
        # aiohttp can stay open until the TrueNAS client is completely closed
        # and then send a nice "shutdown complete" message, instead of just
        # returning a "send and pray" response.
        with suppress(asyncio.CancelledError):
            await self.client_task

    async def status(self) -> dict[str, Any]:

        if not self.ws_conn:
            client_status = "Client failed to start"
        else:
            client_status = f"{self.ws_conn.__class__.__name__} started"

        # NOTE: we intentionally do not wait for the is_connected event here.
        # If a status request comes in, it does not matter if this is in the
        # middle of reconnecting. The status check should return the state
        # RIGHT NOW. We don't wait. We return what it says right now.
        if connected := self.is_connected.is_set():
            start_time = time.time()
            await self({"method": "core.ping", "params": []})
            end_time = time.time()
            ping = f"{(end_time - start_time)*1000:.0f} ms"
        else:
            ping = "not authenticated"
            log.warning("Request received but client is not connected")

        return {
            "client-status": client_status,
            "connected": connected,
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

    def _make_rpc_request(
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

    async def _perform_conn_diag(self) -> ConnDiag:

        results = {}
        # NOTE: These tests handle their own timeouts and return False if a timeout
        # is encountered, there should be no reason this can raise an error
        async for test_name, result in run_connection_diagnostic(self.config):
            results[test_name] = result
            log.info("%s result: %s", test_name, result)
        return ConnDiag(**results)

    async def _get_websocket_conn(self) -> client.WebSocketClientProtocol:

        log.info("Starting _get_websocket_conn")

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # required for wss

        if self.config.validate_certs and self.config.truenas_cert_path:
            cert_path_obj = Path(self.config.truenas_cert_path)
            ssl_context.load_verify_locations(cafile=cert_path_obj)
            log.info("Loaded certificate for validation")
        elif self.config.validate_certs:
            log.info(
                "Validate certs but no cert provided. The server will need to have "
                "a cert must be from a trusted CA for auth to work"
            )
        else:
            ssl_context.check_hostname = False  # must disable this first
            ssl_context.verify_mode = ssl.CERT_NONE
            log.info("Disabled certificate validation")

        self.conn_diag = await self._perform_conn_diag()

        return await client.connect(
            self.config.uri,
            ssl=ssl_context,
            user_agent_header=APP_NAME,
            open_timeout=OPEN_TIMEOUT,
            ping_interval=HEARTBEAT,
            ping_timeout=PING_TIMEOUT,
        )

    async def _error_handler(self, err_string: str, e: Exception):

        if self.config.log_level == "trace":
            log.error(err_string)
            raise
        elif self.config.log_level == "debug":
            log.error(err_string, exc_info=e)
            await self.close()
        else:
            log.error(err_string)
            await self.close()

    async def _connect(self) -> None:

        try:
            self.ws_conn = await self._get_websocket_conn()

            req = self._make_rpc_request(
                "auth.login_with_api_key", [self.config.api_key.get_secret_value()]
            )
            await self.ws_conn.send(json.dumps(req))

            # We have to read from recv manually to ensure we're authenticated
            # before starting the reader loop.
            raw = await self.ws_conn.recv()
            auth_response = json.loads(raw)

            # HACK: This is a very crude way to validate that we authenticated
            # properly. We need to determine a better system
            # TODO: This needs to have a check for why the auth failed (ie.
            # incorrect password)
            if not auth_response.get("result"):
                raise ValueError("Response did not contain key named 'result'")

            log.info("Authenticated.")
            self.is_connected.set()
        except TimeoutError:
            # NOTE: Timeout and OSError are the only errors for which this will simply
            # raise the error to the _connection_loop method and allow it
            # to retry in a loop.
            # The rest of the exceptions are considered unrecoverable and will
            # cause the program to shut down.
            self.conn_diag = await self._perform_conn_diag()
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
            log.error(err_string)
            # raising allows the outer _connection_loop to try again
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
            await self._error_handler(err_str, e)
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
            log.error(err_string)
            raise
        except json.JSONDecodeError as e:
            # HACK: Im not entirely sure under what circumstances this would happen,
            # but I believe it means retrying would be futile. I Might be wrong tho.
            await self._error_handler(f"Malformed response in auth: {e}", e)
        except ValueError as e:
            # This probably indicates a wrong value like the API key was incorrect.
            # as noted in _connect, the handling for this is very crude at the moment
            await self._error_handler(f"Authentication failed: {e}", e)
        except (ws_exceptions.InvalidURI, socket.gaierror) as e:
            err_str = (
                f"Address resolution error: {e} {e.__class__} | Most likely cause is the "
                f"address for TRUENAS_HOST is not correct "
            )
            await self._error_handler(err_str, e)
        else:
            log.debug(self.conn_diag)
            return

    def _cleanup_pending(self):
        "this is run every time the client reconnects to clear old requests"

        err = ConnectionError("Websocket connection dropped")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(err)  # Fail any pending futures
        self.pending.clear()

    async def _connection_loop(self) -> bool:
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
                    # 4 possible scenarios:
                    #   1. No exception: we should be connected, loop will break
                    #   2. bad exception: raise it (its cause of traceback mode)
                    #   3. normal exception (timeouts, etc): backoff and retry
                    #   4. Cancel - just raise
                    await self._connect()
                except asyncio.CancelledError:
                    # Standard cancel, must raise up to _start
                    raise
                except (
                    ssl.SSLError,
                    ws_exceptions.InvalidURI,
                    socket.gaierror,
                    json.JSONDecodeError,
                    ValueError,
                ):
                    # NOTE: The only reason we'd catch one of these here is if we're in
                    # trace mode because trace just raises all exceptions, so we need
                    # to move it along here as well.
                    raise
                except Exception:
                    log.error("Reconnect attempt failed. Retrying in %ss...", delay)
                    await asyncio.sleep(delay)
                    # Exponential backoff
                    delay = min(delay * 2, MAX_RECONNECT_WAIT)

            log.debug("Broke out the connection loop so we must be connected now")
            assert self.is_connected.is_set()
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

        future: asyncio.Future[dict[str, Any]] = self.loop.create_future()

        self.pending[payload["id"]] = future

        try:
            await self.ws_conn.send(json.dumps(payload))
        except Exception:
            # Clean up the pending dict if the send fails instantly, otherwise
            # the request will be stuck in memory
            self.pending.pop(payload["id"], None)
            raise

        # return await future
        try:
            return await asyncio.wait_for(future, timeout=RESPONSE_TIMEOUT)
        except TimeoutError as e:
            # Clean up the pending dict so it doesn't leak
            self.pending.pop(payload["id"], None)
            raise TimeoutError(
                "TrueNAS API failed to respond to the RPC call in time."
            ) from e

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

        except asyncio.CancelledError:
            pass
        except ws_exceptions.ConnectionClosedError as e:
            log.error("Connection closed unexpectedly: %s", e)
        except ws_exceptions.WebSocketException as e:
            log.error("Websocket error: %s %s\n", e, e.__class__)
        except Exception as e:
            log.error("Unknown reader loop error: %s", e)
        finally:
            self.is_connected.clear()
