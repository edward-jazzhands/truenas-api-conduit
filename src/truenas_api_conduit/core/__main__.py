# standard library
import json
import os
import sys
import logging
import signal
import asyncio
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # ws_client contains the import for the websockets library so we gain
    # a little bit by making it a lazy import when its needed.
    from truenas_api_conduit.core.ws_client import TrueNASClient

    # This module will look at the is_config_frozen global to determine if
    # the config is frozen. As such we need to defer importing it until
    # we've had a chance to set that global.
    from truenas_api_conduit.config import Config

# third party
import pydantic
from aiohttp import web
from aiohttp.web_runner import GracefulExit

# project
from truenas_api_conduit import LOCK_FILE, APP_NAME
from truenas_api_conduit.console import console_stderr
import truenas_api_conduit.log_setup as log_setup


def handle_exit(*_):
    print("\nShutting down.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)


log_setup.init_logging()
# log_setup.set_log_level(logging.DEBUG)
log = logging.getLogger(__name__)


class RequestHeader(Enum):
    NONE = 0
    MISSING = 1
    INCORRECT = 2
    CORRECT = 3


def check_request_header(request: web.Request) -> RequestHeader:
    "Check if the request has the required header"

    looking_for: str | None = request.app["config"].request_header

    if looking_for is None:  # this means there is no required header
        return RequestHeader.NONE
    else:
        if incoming_header := request.headers.get(APP_NAME):
            log.info("Found incoming header: %s", incoming_header)
            if incoming_header == looking_for:
                return RequestHeader.CORRECT
            else:
                return RequestHeader.INCORRECT
        else:
            return RequestHeader.MISSING


# in Aiohttp, the callback endpoint functions must always take a web.Request
# object as the only argument.


async def request_handler(request: web.Request) -> web.Response:
    "Take request in json-rpc, return response in json-rpc"

    # This is the callback for the /request endpoint
    # The API for the client is simple. The client class can be
    # called as an awaitable function.

    log.info("Request received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        log.warning("Request did not have the required header")
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        log.warning("Request had the wrong header")
        return web.json_response({"error": "Incorrect header"}, status=400)

    try:
        payload = await request.json()  # JSON-RPC payload
    except json.JSONDecodeError as e:
        log.error("Malformed request, skipping: %s", e)
        return web.json_response({"error": "Malformed request"}, status=400)

    log.info("Request payload: %s", payload)

    client: TrueNASClient = request.app["truenas"]
    result = await client(payload)
    log.info("Request successful")
    log.debug("Response: %s,", result)

    # Return result back to CLI as JSON
    return web.json_response(result)


async def status(request: web.Request) -> web.Response:
    "Check the status of the TrueNAS API Conduit service"

    log.info("Status request received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        log.warning("Request did not have the required header")
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        log.warning("Request had the wrong header")
        return web.json_response({"error": "Incorrect header"}, status=400)

    client: TrueNASClient = request.app["truenas"]
    result = await client.status()
    log.info("Status request successful")
    return web.json_response(result)


async def _shutdown() -> None:
    await asyncio.sleep(0.1)
    raise GracefulExit()


async def shutdown(request: web.Request) -> web.Response:

    log.info("Shutdown command received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        log.warning("Request did not have the required header")
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        log.warning("Request had the wrong header")
        return web.json_response({"error": "Incorrect header"}, status=400)

    asyncio.ensure_future(_shutdown())
    return web.json_response({"result": "Shutting down"})


async def restart(request: web.Request) -> web.Response:

    log.info("Restart command received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        log.warning("Request did not have the required header")
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        log.warning("Request had the wrong header")
        return web.json_response({"error": "Incorrect header"}, status=400)

    async def _restart() -> None:
        await asyncio.sleep(0.2)
        await request.app.cleanup()
        os.environ["TAC_CONFIG"] = request.app["config"].model_dump_json()
        dname = APP_NAME + "d"  # ex: my-appd
        os.execvp(dname, [dname])

    asyncio.ensure_future(_restart())
    return web.json_response({"result": "Restarting"})


# in Aiohttp, the startup and cleanup hooks will always have the app instance
# passed in to them as the first argument.


async def start_truenas(app: web.Application) -> None:

    from truenas_api_conduit.core.ws_client import TrueNASClient
    from truenas_api_conduit.config import Config

    cfg = app["config"]
    assert isinstance(cfg, Config)
    if cfg.log_level not in ("trace", "debug"):
        # The CLI only has timestamps for debug or trace but the service should
        # always have timestamps
        log_setup.enable_timestamps()

    log.info("Starting TrueNAS API websocket client")
    client = TrueNASClient(cfg)  #  The client runs inside the web app
    app["truenas"] = client

    if not await client.connection_loop():  #  will handle the auth process
        log.critical("Something else has the reconnection lock! Exiting program.")
        asyncio.ensure_future(_shutdown())
        return

    if os.path.exists(LOCK_FILE):
        log.warning("Lockfile was not properly cleaned up after last run")

    cfg_dict = {
        "pid": os.getpid(),
        "address": cfg.service_address,
        "socket_port": cfg.socket_port,
        "header": cfg.request_header,
    }

    with open(LOCK_FILE, "w") as f:
        f.write(json.dumps(cfg_dict, indent=2))

    # windows ACLs are a pain and would require an entire third party library
    # just for this purpose. So windows users just get slightly shittier security.
    # Thats the way she goes bubs.
    LOCK_FILE.chmod(0o600)  # HACK: This won't do anything on windows.


async def stop_truenas(app: web.Application) -> None:

    log.info("Running cleanup")
    client: TrueNASClient = app["truenas"]
    await client.close()
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass


async def main(cfg: Config) -> None:

    log.info("Starting to initialize the HTTP server")

    app = web.Application()

    app["config"] = cfg

    # HTTP endpoints for the CLI
    app.router.add_post("/request", request_handler)
    app.router.add_get("/status", status)
    app.router.add_post("/shutdown", shutdown)
    app.router.add_post("/restart", restart)

    app.on_startup.append(start_truenas)  # sets up websocket client
    app.on_cleanup.append(stop_truenas)  # closes websocket client

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=cfg.service_address, port=cfg.socket_port)
    await site.start()

    log.info("HTTP server started")

    try:
        await asyncio.Event().wait()
    except GracefulExit:
        pass
    finally:
        await runner.cleanup()


def error_handler(err_string: str, log_level: str):

    if log_level.lower() == "debug":
        log.exception(err_string)
        sys.exit(1)
    elif log_level.lower() == "trace":
        log.error(err_string)
        raise
    else:
        log.error(err_string)
        sys.exit(1)


def start():

    # NOTE: on the CLI side I allow the model to not be frozen. The CLI
    # can modify some settings in the config while it's building it.
    # But once it comes time to run the program, I freeze the config.
    # This is basically just security hygiene, makes it harder for a
    # hypothetical hacker to modify the config while the service is running.
    from truenas_api_conduit.app_globals import set_config_frozen

    set_config_frozen()

    nc_env = os.environ.get("NO_COLOR")
    if nc_env is not None:
        console_stderr.no_color = True

    # recall the Config class is a pydantic-settings model
    from truenas_api_conduit.config import Config

    try:
        raw = ""
        if not sys.stdin.isatty():  # piped start, OS startup, etc
            log.info("Detected not a TTY, checking stdin...")
            raw = sys.stdin.read()
        if raw:
            log.info("Detected input on stdin, loading from stdin")
            cfg = Config.model_validate_json(raw)
        else:
            log.info("No input on stdin, loading normally")
            cfg = Config()
    except json.JSONDecodeError as e:
        log.critical("Malformed config JSON: %s", e)
        sys.exit(1)
    except pydantic.ValidationError as e:
        log.critical("Configuration error: %s", e)
        sys.exit(1)

    log_level: int = logging.getLogger().level
    level_mapping = logging.getLevelNamesMapping()
    if log_level >= level_mapping["WARNING"]:
        log.warning(
            "The service will show you very little information when the logging "
            "level is set to warning or higher"
        )
    level_name = logging.getLevelName(log_level)
    log.info("Logging level is currently at %s", level_name)
    log.debug("Config: %s", cfg)
    log.debug("Config provenance: %s", cfg.provenance)

    try:
        asyncio.run(main(cfg), debug=(level_name.lower() == "trace"))
    except OSError as e:
        err_string = f"{getattr(e, '__module__', 'none')}.{repr(e)} "
        err_string += str(e) if str(e) else ""
        if e.strerror:
            err_string += f": {e.strerror}"
        if e.errno:
            err_string += f"  (Code: {e.errno})"
        if e.__context__:
            full_context = (
                f"{getattr(e.__context__, '__module__', 'none')}.{repr(e.__context__)}"
            )
            err_string += f"\n  Occurred while handling: {full_context}"
        if e.__cause__:
            full_cause = (
                f"{getattr(e.__cause__, '__module__', 'none')}.{repr(e.__cause__)}"
            )
            err_string += f"\n  Caused by: {full_cause}"
        error_handler(err_string, level_name)
    except Exception as e:
        error_handler(str(e), logging.getLevelName(log_level))


if __name__ == "__main__":
    start()
