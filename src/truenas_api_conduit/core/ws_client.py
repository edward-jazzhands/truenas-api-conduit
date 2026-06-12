# standard library
import asyncio
from typing import Any, Final, TYPE_CHECKING
import json
import ssl
from pathlib import Path
import logging
import time
import socket
from dataclasses import dataclass

if TYPE_CHECKING:
    from truenas_api_conduit.core.msg_receiver import MessageReceiver

# third party
import websockets.client as client
import websockets.exceptions as ws_exceptions

# project
from truenas_api_conduit import APP_NAME
from truenas_api_conduit.app_globals import app_env
from truenas_api_conduit.core import examine_os_error
from truenas_api_conduit.errors import ConduitError
from truenas_api_conduit.config import Config
from truenas_api_conduit.core.conn_diag import ConnDiag, run_connection_diagnostic

OPEN_TIMEOUT: Final[int] = 5  #         when creating the websocket client
REQUEST_WAIT_TIME: Final[int] = 10  #   how long to wait for response in a call
HEARTBEAT: Final[int] = 30  #           from client to TrueNAS
PING_TIMEOUT: Final[int] = 10  #        how long to wait if a heartbeat fails
MAX_RECONNECT_WAIT: Final[int] = 60  #  max wait between reconnections if connection drops
SHUTDOWN_TIMEOUT: Final[int] = 5  #     how long to wait for the client task to finish

log = logging.getLogger(__name__)


__all__ = [
    "TrueNASClient",
    "CloseResult",
]


@dataclass
class CloseResult:
    # False, "Tried to close, but there's no client task"
    # False, "Unexpected error closing the client task"
    # True, "Client closed successfully"
    is_closed: bool
    msg: str


class UnexpectedShutdown(ConduitError):
    pass


class TrueNASClient:
    """wrapper around the websocket connection. This is created by the aiohttp web
    server, and shares aiohttp's event loop (must be passed in)"""

    def __init__(
        self,
        config: Config,
        loop: asyncio.AbstractEventLoop,
        message_receiver: MessageReceiver,
    ) -> None:

        self.config: Config = config
        "pydantic-settings Config object"

        self.loop: asyncio.AbstractEventLoop = loop
        "the asyncio event loop"

        self.message_receiver: MessageReceiver = message_receiver
        "Callable class to send messages from this client to the aiohttp server"

        self.ws_conn: client.WebSocketClientProtocol | None = None
        "Websocket connection client"

        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        """Maps request IDs -> asyncio Futures waiting for responses.
        This is how HTTP requests get their matching websocket response"""

        self.req_id: int = 1
        "increments one with each request"

        self.is_connected: asyncio.Event = asyncio.Event()
        "Signals when the websocket is ready to send data."

        self._reconnecting: bool = False
        "Ensures only one reconnect loop runs at a time."

        self.req_id_lock: asyncio.Lock = asyncio.Lock()
        "Ensures only one function can grab and increment the request ID at a time"

        self.client_task: asyncio.Task | None = None
        "The asyncio task that runs the client loop"

        self._closing: bool = False
        "Flag set by the close() method to indicate that the client is closing"

        self.conn_diag: ConnDiag | None = None
        """The connection diagnostics object. Runs every reconnection attempt
        as well as again if a TimeoutError is raised"""

    # PUBLIC API SECTION
    # It's important nothing in the public API can raise errors back to
    # the caller / module that's implementing this class. We want to keep
    # a clean separation of concerns and not allow errors to crash the
    # outer service (aiohttp), but rather just provide helpful messages.

    def start(self) -> asyncio.Task:
        "returns the created task"
        # NOTE: We need to store a reference of the task as a self attribute
        # so that it does not get garbage collected by python
        self.client_task = asyncio.create_task(self._start())
        return self.client_task

    async def close(self) -> CloseResult:
        """used to gracefully close the client and shut down the program.
        This should only be called by the aiohttp server, not internally."""

        log.debug("Starting close() method")

        # NOTE: This will be called in two scenarios I can think of:
        # 1) If the client lifecycle task finishes for any reason, it will trigger a
        #    callback which will start the service cleanup
        # 2) If the server receives a stop command or a kill signal, it will set
        #    the shutdown_event and trigger the service cleanup

        # (service cleanup is the cleanup/teardown section of the context manager
        # for the TrueNASClient class in the aiohttp server)

        # In scenario 1, the client lifecycle task will already be finished.
        # In scenario 2, the client lifecycle task will still be running.

        if not self.client_task:
            # I believe this should never happen under normal conditions
            return CloseResult(False, "Tried to close, but there's no client task")

        self._closing = True

        # recall that cancel does not actually turn anything off immediately,
        # it just gets asyncio to raise a CancelledError inside of the running
        # task, which will be raised by asyncio at the very next thing in
        # the task that tries to await something (its asyncio magic, dont ask
        # too many questions, you just have to believe)
        if not self.client_task.cancelling():  # returns 0 (falsy) if not cancelling
            self.client_task.cancel()

        # Now, we can safely close the websocket connection without affecting
        # the active reader loop. After shutting down the connection, that
        # CancelledError we queued up will get caught in the reader loop, which will
        # stop itself. After the reader loop finishes, the lifecycle method
        # _start will restart its while loop, and see the _closing flag is set.
        if self.ws_conn:
            try:
                await self.ws_conn.close(reason="Server shutting down")
            except Exception as e:
                # literally any error that could happen here should not stop
                # the program. The OS will clean up the connection anyway.
                log.error("Error closing the websocket connection: %s", e)

        # Last we have to await the task to block here until it finishes.
        # This is necessary to give the CancelledError we just triggered the time
        # it needs to bubble up through the program and allow the client to gracefully
        # close itself. This way, aiohttp can stay open until the TrueNAS client is
        # completely closed and then send a nice message that says "the client has
        # closed gracefully", instead of just praying it worked.
        # ALSO: This is safe to run if the task has already finished.
        try:
            await asyncio.wait_for(self.client_task, timeout=SHUTDOWN_TIMEOUT)
        except asyncio.CancelledError:
            log.debug(
                "Caught a cancelled error while waiting for the client task to close"
            )
        except Exception as e:
            # If we encountered an error waiting for the client to clean itself up,
            # something is borked and the caller needs to force an exit
            log.error("Unexpected error closing the client task: %s", e)
            return CloseResult(
                False, f"Error while closing the internal TrueNAS client: {e}"
            )

        return CloseResult(True, "Client closed successfully")

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
            response = await self.call(
                {"method": "core.ping", "params": []}
            )  # handles the timeout
            if response["result"] in ("FAILED", "TIMEOUT"):
                ping = f"Timed out after {REQUEST_WAIT_TIME} seconds"
            else:
                end_time = time.time()
                ping = f"{(end_time - start_time)*1000:.0f} ms"
        else:
            ping = "Not connected"
            log.warning("Request received but client is not connected")

        return {
            "client-status": client_status,
            "connected": connected,
            "client-server ping": ping,
            "service mode": app_env,
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

    # NOTE: Just to refresh your brain on how this works if you're rusty, in a
    # proper websocket client architecture, the sending logic and the receiving logic
    # are separated into two different concerns. The sending logic is the "writer"
    # and the receiving logic is the "reader". Technically, sending and receiving
    # are two different streams, websockets just abstract that into one interface.
    # That's why we need the pending dict. call() just fires off the request
    # and returns a future.

    async def call(self, payload: dict[str, Any]) -> dict[str, Any]:

        if not self.ws_conn:
            msg = "Request received but client is not connected"
            log.warning(msg)
            return {"result": "NOT CONNECTED", "error": msg}

        # check connection is alive before making request
        try:
            # The request wait time adds a buffer in case its in the middle of
            # trying to reconnect
            await asyncio.wait_for(self.is_connected.wait(), timeout=REQUEST_WAIT_TIME)
        except TimeoutError as e:
            msg = "TrueNAS API request timed out waiting for websocket reconnection."
            if self.config.log_level == "trace":
                raise TimeoutError(msg) from e
            else:
                log.error(msg)
                return {"result": "TIMEOUT", "error": msg}

        # I suspect locking the request maker is not actually necessary here,
        # but the bug finders keep going off if this is not done with a lock.
        # And there definitely should not be any need for a timeout here.
        async with self.req_id_lock:
            rpc_payload = self._make_rpc_request(payload["method"], payload["params"])

        future: asyncio.Future[dict[str, Any]] = self.loop.create_future()
        self.pending[rpc_payload["id"]] = future

        try:
            send_fut = self.ws_conn.send(json.dumps(rpc_payload))
            await asyncio.wait_for(send_fut, timeout=REQUEST_WAIT_TIME)
        except Exception as e:
            # Clean up the pending dict if the send fails instantly, otherwise
            # the request will be stuck in memory
            self.pending.pop(rpc_payload["id"], None)
            msg = "Failed to send the request to the TrueNAS server."
            if self.config.log_level == "trace":
                raise ConnectionError(msg) from e
            else:
                log.error(msg)
                return {"result": "FAILED", "error": msg}

        # return await future
        try:
            return await asyncio.wait_for(future, timeout=REQUEST_WAIT_TIME)
        except TimeoutError as e:
            # Clean up the pending dict so it doesn't leak
            self.pending.pop(payload["id"], None)
            msg = "Timed out waiting for a response from the TrueNAS server."
            if self.config.log_level == "trace":
                raise TimeoutError(msg) from e
            else:
                log.error(msg)
                return {"result": "TIMEOUT", "error": msg}

    # PUBLIC API ABOVE THIS LINE
    # *-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+-+H+
    # INTERNAL METHODS BELOW

    # used in _connect and call()
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

    # used in _start
    def _cleanup_pending(self):
        """this is run to clear old requests every time the reader loop breaks
        and the client restarts the full lifecycle (_start)"""

        err = ConnectionError("Websocket connection dropped")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(err)  # Fail any pending futures
        self.pending.clear()
        #! Consier: Should we be destroying the ws_conn object here as well?:
        # it will be recreated by the next connection loop so I believe this
        # is good to remove it here. It ensures the object is not lingering
        # between reconnections attempts.
        self.ws_conn = None

    # used in start()
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
            # If for some reason this was not caused by the close() command (which
            # triggers the CancelledError), or by an unrecoverable error (triggers
            # UnexpectedShutdown), then the while loop will restart
            except asyncio.CancelledError:
                log.warning("Websocket client received a cancel command")
                break
            except UnexpectedShutdown as e:
                log.error(f"Program has to close due to an unrecoverable error: {e}")
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

        log.info("Exiting the TrueNAS client task")
        # When this task completes it will trigger the callback set with
        # loop.add_done_callback() in the aiohttp web server, which will
        # trigger the server to shutdown.

    # Used in _start
    async def _connection_loop(self) -> bool:
        """False means something else has the reconnect flag set. True
        means it worked. Otherwise loops forver."""

        if self._reconnecting:  # only one at a time
            return False

        log.info("Initiating websocket connection sequence...")

        # NOTE: This will just keep retrying forever. That's intentional,
        # since this is a background service that is expected behavior and
        # good user UX.

        delay = 2
        while not self.is_connected.is_set():
            try:
                await self._connect()
            # UNRECOVERABLES:
            except (
                asyncio.CancelledError,  #  Injected when the asyncio task is cancelled
                UnexpectedShutdown,  #   Our custom exception, signals unrecoverable error
                json.JSONDecodeError,  #   ! is this one recoverable?
                ValueError,  #   TODO: should be used to indicate wrong API key?
                ssl.SSLError,  #   NOTE: The only reason we'd catch one of the rest here
                ws_exceptions.InvalidURI,  # is if we're in trace mode
                socket.gaierror,  #   trace just raises all exceptions
            ):
                raise
            # RECOVERABLES (everything else):
            except Exception:
                # Anything not one of the above should be recoverable, and we
                # will retry in a loop.
                log.error("Reconnect attempt failed. Retrying in %ss...", delay)
                await asyncio.sleep(delay)
                # Exponential backoff
                delay = min(delay * 2, MAX_RECONNECT_WAIT)

        log.debug("Broke out the connection loop so we must be connected now")
        assert self.is_connected.is_set()
        return True

    # Used in _connect and in _get_websocket_conn
    async def _perform_conn_diag(self) -> ConnDiag:

        results = {}
        # NOTE: These tests handle their own timeouts and return False if a timeout
        # is encountered, there should be no reason this can raise an error. They
        # also run in their own threads with loop.run_in_executor, so they run
        # concurrently and this is basically instant if the connection is good.
        async for test_name, result in run_connection_diagnostic(self.config):
            results[test_name] = result
            log.info("%s result: %s", test_name, result)
        return ConnDiag(**results)

    # Used in _connect
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

    # Used in _connect
    async def _error_handler(self, err_string: str, e: Exception):

        # Setting _closing will cause the lifecycle task to exit the loop,
        # the task will then finish, triggering the callback to run the
        # close() method, do the cleanup, and then shutdown the program.
        self._closing = True
        if self.config.log_level == "trace":
            log.error(err_string)
            raise  # trace just lets it bubble up directly
        elif self.config.log_level == "debug":
            log.error(err_string, exc_info=e)
            raise UnexpectedShutdown(err_string) from e
        else:
            log.error(err_string)
            raise UnexpectedShutdown(err_string) from e

    # Used in _connection_loop
    async def _connect(self) -> None:
        "creates a new self.ws_conn object every time this runs"

        try:
            self.ws_conn = await self._get_websocket_conn()

            async with self.req_id_lock:
                req = self._make_rpc_request(
                    "auth.login_with_api_key", [self.config.api_key.get_secret_value()]
                )
            await self.ws_conn.send(json.dumps(req))

            # We have to read from recv manually to ensure we're authenticated
            # before starting the reader loop.
            # ! FIXME: I'm pretty sure this needs to have its own error handling.
            raw = await self.ws_conn.recv()
            auth_response = json.loads(raw)

            # HACK: This is a very crude way to validate that we authenticated
            # properly. We need to determine a better system
            # ! FIXME: This needs to have a check for why the auth failed (ie.
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
            err_string = examine_os_error(e)
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

    # Used in _start
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

                # The future was awaited in the call() method. So when we
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
