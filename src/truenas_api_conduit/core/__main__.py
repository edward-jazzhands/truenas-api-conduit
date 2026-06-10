# standard library
import json
import os
import sys
import logging
import asyncio
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # This module will look at the is_config_frozen global to determine if
    # the config is frozen. As such we need to defer importing it until
    # we've had a chance to set that global.
    from truenas_api_conduit.config import Config
    from truenas_api_conduit.core.ws_client import TrueNASClient

# third party
import pydantic
from aiohttp import web
from aiohttp.web_runner import GracefulExit

# project
from truenas_api_conduit import LOCK_FILE
from truenas_api_conduit.console import console_stderr
import truenas_api_conduit.log_setup as log_setup
import truenas_api_conduit.core.endpoints as endpoints

#! NOT ASYNC
# def handle_exit(*_):
#     print("\nShutting down.")
#     sys.exit(0)

# signal.signal(signal.SIGINT, handle_exit)
# signal.signal(signal.SIGTERM, handle_exit)

# if sys.platform != "win32":
#     signal.signal(signal.SIGHUP, handle_exit)
#     signal.signal(signal.SIGQUIT, handle_exit)


log_setup.init_logging()
log = logging.getLogger(__name__)


# def handle_async_exit(client: TrueNASClient):
#     log.info("Received OS shutdown signal.")
#     await client.close()
#     log.info("Websocket client shutdown successfully")
#     # NOTE: once the client has shut itself down gracefully, the aiohttp
#     # server should run the teardown in truenas_context_manager 


def create_lockfile(cfg: Config):

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


async def truenas_context_manager(app: web.Application):

    # NOTE: This uses the "context manager generator" pattern. It must have
    # exactly one yield, dividing the function in half. The first half
    # is the setup and the second half is the teardown. This convention
    # is set by aiohttp and is required to use app.cleanup_ctx list

    from truenas_api_conduit.core.ws_client import TrueNASClient
    from truenas_api_conduit.config import Config  # imports pydantic

    cfg = app["config"]
    assert isinstance(cfg, Config)
    if cfg.log_level not in ("trace", "debug"):
        # The CLI only has timestamps for debug or trace but the service should
        # always have timestamps
        log_setup.enable_timestamps()

    log.info("Starting TrueNAS API websocket client")
    loop = asyncio.get_running_loop()
    client = TrueNASClient(cfg, loop)
    app["truenas_client"] = client
    create_lockfile(cfg)

    # try:
    #     loop.add_signal_handler(signal.SIGINT, handle_async_exit, client)
    #     loop.add_signal_handler(signal.SIGTERM, handle_async_exit, client)
    # except NotImplementedError:
    #     # this will happen on windows
    #     pass
    #     #! Do we want to register these?
    #     # loop.add_signal_handler(signal.SIGHUP, handle_async_exit)
    #     # loop.add_signal_handler(signal.SIGQUIT, handle_async_exit)

    # NOTE: This method creates and manages its own background task with
    # asyncio.create_task.
    app["truenas_task"] = client.start()

    # The 'wrap yield in try/finally' pattern. Its kind of a brainfuck
    # because we've essentially turned the entirely of the program
    # outside this function (defined by "yield" here) into a single
    # command, which we can now catch and chuck a finally behind.
    # So if any error occurs during the rest of the program before this
    # function resumes control to cleanup, this will catch it to run our
    # finally block.
    # Half of me thinks this is awesome and the other half is like
    # "what in the ever loving fuck, why is this possible"

    try:
        yield
    except (GracefulExit, asyncio.CancelledError):
        log.warning("Server shutdown interrupted TrueNAS")
    finally:
        # <>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>
        # TEARDOWN
        await client.close()

        log.info("Running aiohttp teardown")
        try:
            os.remove(LOCK_FILE)
        except FileNotFoundError:
            pass


async def main(cfg: Config) -> None:

    log.info("Starting to initialize the HTTP server")

    app = web.Application()
    app["config"] = cfg

    app.router.add_post("/request", endpoints.request_handler)
    app.router.add_get("/status", endpoints.status)
    app.router.add_post("/shutdown", endpoints.shutdown)
    app.router.add_post("/restart", endpoints.restart)

    # manages the lifecycle
    app.cleanup_ctx.append(truenas_context_manager)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=cfg.service_address, port=cfg.socket_port)
    await site.start()

    log.info("HTTP server started")

    try:
        await asyncio.Event().wait()
    except (GracefulExit, asyncio.CancelledError):
        log.info("Received exit command")
        pass  #  we pass here to let runner.cleanup() run
    finally:
        # will run the teardown in truenas_context_manager:
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
            "level is set to warning or higher. Set logging to info or use the -v "
            "flag to see more information."
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
