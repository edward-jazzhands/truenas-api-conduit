# standard library
import json
import os
import sys
import logging
import asyncio
import signal
from typing import TYPE_CHECKING, Callable, Any
from asyncio import AbstractEventLoop

if TYPE_CHECKING:
    from truenas_api_conduit.config import AppBaseConfig
    from truenas_api_conduit.core.ws_client import TrueNASClient

# third party
import pydantic
from aiohttp import web

# project
from truenas_api_conduit.app_globals import app_globals
from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.log_setup import logging_manager_factory
from truenas_api_conduit.constants import AppEnv, LOCK_FILE
from truenas_api_conduit.core.unlocker import Unlocker
import truenas_api_conduit.core as core
import truenas_api_conduit.core.endpoints as endpoints

if app_env_str := os.environ.get("TRUENAS_APP_ENV"):
    try:
        appenv_enum = AppEnv(app_env_str)
    except Exception:
        print(f"ERROR: TRUENAS_APP_ENV Environment variable is not valid: {app_env_str}")
        sys.exit(1)
else:
    # If the env var is not set it probably means the user ran the
    # truenas-api-conduitd entrypoint directly. We can just set it
    # for them and continue
    appenv_enum = AppEnv.STANDALONE


logging_manager = logging_manager_factory.get_logging_manager(app_env=appenv_enum)
logging_manager.init_logging(service=True)
log = logging.getLogger(__name__)


async def truenas_context_manager(app: web.Application):

    # NOTE: This uses the "context manager generator" pattern. It must have
    # exactly one yield, dividing the function in half. The first half
    # is the setup and the second half is the teardown. This convention
    # is set by aiohttp and is required to use app.cleanup_ctx

    from truenas_api_conduit.config import AppBaseConfig  # imports pydantic

    cfg = app["config"]
    log.info("cfg type: %(cfg_type)s", {"cfg_type": cfg.__class__.__name__})

    unlocker: Unlocker = app["unlocker"]
    core.create_lockfile(LOCK_FILE=LOCK_FILE, cfg=cfg)

    if not isinstance(cfg, AppBaseConfig):
        raise RuntimeError(f"Config object is not valid: {cfg.__class__.__name__}")

    if (
        cfg.log_level not in ("trace", "debug")
        and app_globals.app_env == AppEnv.STANDALONE
    ):
        # The CLI only has timestamps for debug or trace, but the service should
        # always have timestamps when running in standalone mode. In OS mode
        # or docker, the OS/docker will handle timestamps
        logging_manager.enable_timestamps()

    # NOTE: Requests will check if this is None, if so this will be used
    # as the indicator that the app is in locked mode
    app["truenas_client"] = None

    log.info("cfg.start_locked: %(start_locked)s", {"start_locked": cfg.start_locked})
    if cfg.start_locked:
        log.warning("Starting app in locked mode")
        app["locked"] = True
        if app["json_dict"]:
            log.warning(
                "App is starting in locked mode, but config was passed in. "
                "This config will be stored until the app is unlocked."
            )
    else:
        # Recall that 'json_dict' can only come from --standalone starts, or
        # restarts triggered by the /restart endpoint with hot reloading
        if app["json_dict"]:
            log.info("loading config from stdin")
            unlock_result = await unlocker.unlock_dict(app["json_dict"])
        else:
            # no config passed in, not starting locked
            unlock_result = await unlocker.unlock()

        if unlock_result is True:
            log.info("Unlock successful")
        else:
            log.error("Unlock attempt failed!: %(unlock_result)s", {"unlock_result": unlock_result})
            log.warning("The service will start in locked mode")

    # The 'wrap yield in try/finally' pattern. Its kind of a brainfuck
    # because we've essentially turned the entirely of the program
    # outside this function (defined by "yield" here) into a single
    # command, which we can now catch and chuck a finally behind.
    # So if any error occurs during the rest of the program before this
    # function resumes control to cleanup, this will catch it to run our
    # finally block.
    # Half of me thinks this is awesome and the other half is like
    # "what in the fuck, why is this possible"

    try:
        yield
    finally:
        # *<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>-+-<>
        # TEARDOWN - this is equivalent to aiohttp's on_cleanup hook

        log.info("Running service teardown")
        if result := core.delete_lockfile(LOCK_FILE):
            log.error("Failed to delete lockfile: %(result)s", {"result": result})

        client: TrueNASClient | None = app.get("truenas_client")
        if client:
            close_result = await client.close()
            log.debug(close_result)
            if close_result.is_closed:
                log.info("The TrueNAS websocket client closed itself gracefully")
            else:
                log.warning(close_result.msg)
        else:
            if app["locked"]:
                log.warning("Shutting down while in locked mode")
            else:
                log.warning("There's no TrueNAS websocket client to close down")
        app["config"] = None


async def main(cfg: AppBaseConfig, json_dict: dict[str, Any] | None = None) -> None:

    log.info("Starting to initialize the HTTP server")

    app = web.Application()
    app["config"] = cfg

    if json_dict:
        app["json_dict"] = json_dict
    else:
        app["json_dict"] = None

    shutdown_event = asyncio.Event()
    app["shutdown_event"] = shutdown_event

    app["unlocker"] = Unlocker(app)
    app["locked"] = True  # always start locked

    def handle_async_exit():
        log.info("Received OS shutdown signal.")
        app["shutdown_event"].set()

    loop = asyncio.get_running_loop()
    try:
        # NOTE: This is the same thing that handle_signals arg on the AppRunner
        # class already does, but this gives me more control over the shutdown signal
        loop.add_signal_handler(signal.SIGINT, handle_async_exit)
        loop.add_signal_handler(signal.SIGTERM, handle_async_exit)
    except NotImplementedError:
        # Windows asyncio doesn't support add_signal_handler natively.
        # It relies on standard KeyboardInterrupt bubbling up to asyncio.run(main())
        log.warning(
            "Signal handlers not supported on this OS. Relying on default interrupts."
        )

    app.router.add_post("/request", endpoints.request_handler)
    app.router.add_get("/status", endpoints.status)
    app.router.add_post("/stop", endpoints.stop)
    app.router.add_post("/restart", endpoints.restart)
    app.router.add_post("/unlock", endpoints.unlock)
    app.router.add_post("/lock", endpoints.lock)

    # manages the lifecycle
    app.cleanup_ctx.append(truenas_context_manager)

    host, port = cfg.conduit_host.split(":")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=int(port))
    await site.start()

    log.info("HTTP server started")

    try:
        await shutdown_event.wait()
    finally:
        # will run the teardown in truenas_context_manager:
        await runner.cleanup()


def error_handler(err_string: str, log_level: str, e: BaseException):

    log.error(
        "Uncaught exception reached top-level exception handler."
        "This usually indicates a software bug or an unexpected condition the "
        "application could not safely handle. "
        "The application will now exit."
    )
    if log_level.lower() == "trace":
        log.error(err_string)
        raise e
    else:
        log.error(err_string)
        sys.exit(1)


# The loop factory is for usage during testing / unit tests
def start(loop_factory: Callable[[], AbstractEventLoop] | None = None):

    # NOTE: on the CLI side I allow the model to not be frozen. The CLI
    # can modify some settings in the config while it's building it.
    # But once it comes time to run the program, I freeze the config.
    # This is basically just security hygiene, makes it harder for a
    # hypothetical hacker to modify the config while the service is running.
    app_globals.set_config_frozen()

    if os.environ.get("NO_COLOR"):
        console_stderr.no_color = True

    # recall the Config class is a pydantic-settings model
    from truenas_api_conduit.config import AppBaseConfig

    json_dict: dict[str, Any] | None = None
    cfg: AppBaseConfig | None = None

    if not sys.stdin.isatty():  # piped start, OS startup, etc
        log.debug("Detected not a TTY, checking stdin...")
        if raw := sys.stdin.read():
            log.debug("Detected input on stdin. Parsing JSON...")
            try:
                json_dict = json.loads(raw)
            except json.JSONDecodeError as e:
                log.critical("Malformed config JSON: %(e)s", {"e": e})
                sys.exit(1)
        else:
            log.debug("No input on stdin")

    try:
        cfg = AppBaseConfig()
    except pydantic.ValidationError as e:
        log.critical("Configuration error: %(e)s", {"e": e})
        sys.exit(1)

    level_mapping = logging.getLevelNamesMapping()
    level_int = level_mapping[cfg.log_level.upper()]
    logging_manager.set_log_level(level_int)

    if level_int >= level_mapping["WARNING"]:
        log.warning(
            "The service will show you very little information when the logging "
            "level is set to warning or higher. Set logging to info or use the -v "
            "flag to see more information."
        )
    level_name = logging.getLevelName(level_int)
    log.info("Logging level is currently at %(level_name)s", {"level_name": level_name})

    log.info("Base Config: %(cfg)s", {"cfg": cfg})

    app_globals.set_app_env(appenv_enum)
    log.debug("App env set to: %(app_env)s", {"app_env": app_globals.app_env})
    if app_globals.app_env is None:
        raise ValueError("Tried running app with no app_env set")

    try:
        asyncio.run(
            main(cfg, json_dict),
            debug=(level_name.lower() == "trace"),
            loop_factory=loop_factory,
        )
    except OSError as e:
        # If we got an OSError or other exception at this point then either
        # we're in traceback mode, or something is very wrong.
        err_string = core.examine_error(e)
        error_handler(err_string, level_name, e)
    except Exception as e:
        error_handler(str(e), logging.getLevelName(level_int), e)
    finally:
        log.warning("Program shutting down now")


if __name__ == "__main__":
    start()
