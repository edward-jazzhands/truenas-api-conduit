# standard library
import json
import os
import sys
import logging
import asyncio
import signal
import tomllib
from typing import TYPE_CHECKING, Callable, assert_never
from asyncio import AbstractEventLoop

if TYPE_CHECKING:
    # This module will look at app_globals.is_config_frozen to determine if
    # the config is frozen. As such we need to defer importing it until
    # we've had a chance to set that global.
    from truenas_api_conduit.config import Config, AppBaseConfig


# third party
import pydantic
from aiohttp import web
from aiohttp.web_runner import GracefulExit

# project
from truenas_api_conduit import LOCK_FILE
import truenas_api_conduit.core as core
from truenas_api_conduit.app_globals import app_globals
from truenas_api_conduit.console import console_stderr
import truenas_api_conduit.log_setup as log_setup
import truenas_api_conduit.core.endpoints as endpoints

log_setup.init_logging(service=True)
log = logging.getLogger(__name__)


class Unlocker:

    def __init__(self, app: web.Application) -> None:
        self.app = app

    async def unlock(self, crypt_key: str) -> bool | Exception:

        log_level = logging.getLogger().level
        log_mapping = logging.getLevelNamesMapping()

        from truenas_api_conduit.config import Config
        from truenas_api_conduit.config.file_encrypter import (
            PasswordGetError,
            GetErrorEnum,
        )

        try:
            cfg = Config(crypt_key=pydantic.SecretStr(crypt_key))
        except pydantic.ValidationError as e:
            return e
        except tomllib.TOMLDecodeError as e:
            return e
        except PasswordGetError as e:
            # This is my custom error class so it will only happen if keyring tried
            # to use my fallback FileEncrypter backend, and the user password was
            # incorrect. Or a bug happened.
            err_string: str | None = None
            if e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
                return e
            else:
                # NOTE: The only two GetErrorEnums that will actually trigger the
                # PasswordGetError to be raised are INCORRECT_ENCRYPTION_KEY and
                # GENERIC_ERROR. So if it wasn't the first one then we must assume
                # its a bug.
                if log_level <= log_mapping["TRACE"]:
                    raise
                else:
                    log.error(
                        "Unexpected error: %s | Raise the verbosity to see more information"
                    )
                    self.app["shutdown_event"].set()
                    return e
        except Exception as e:
            if log_level <= log_mapping["TRACE"]:
                raise
            else:
                err_string = (
                    "[default]Could not initialize config:\n\n"
                    f"    {e} ({e.__class__.__qualname__})\n\n"
                    "Raise the verbosity to see more information."
                )
                log.critical(err_string)
                self.app["shutdown_event"].set()
                return e
        else:
            log.info("Config loaded successfully")
            config_str = ""
            for field, value in cfg.model_dump().items():
                new_section = f"\n{field}: {value}"
                new_section += " " * (35 - len(new_section))
                new_section += f"(from {cfg.provenance[field]})"
                config_str += new_section
            log.info(config_str)

            client_startup(cfg, self.app)
            return True


def create_lockfile(cfg: Config):

    if os.path.exists(LOCK_FILE):
        log.warning("Lockfile was not properly cleaned up after last run")
    log.debug("Creating lockfile")

    assert app_globals.app_env is not None, "Tried running app with no app_env set"
    cfg_dict = {
        "pid": os.getpid(),
        "address": cfg.service_address,  # these 3 cfg items are all in AppBaseConfig
        "socket_port": cfg.socket_port,
        "header": cfg.request_header,
        "app_env": str(app_globals.app_env.value),
    }

    with open(LOCK_FILE, "w") as f:
        f.write(json.dumps(cfg_dict, indent=2))

    # windows ACLs are a pain and would require an entire third party library
    # just for this purpose. So windows users just get slightly shittier security.
    # Thats the way she goes bubs.
    LOCK_FILE.chmod(0o600)  # HACK: This won't do anything on windows.


def client_startup(cfg: Config, app: web.Application):

    log.info("Starting TrueNAS API websocket client")

    from truenas_api_conduit.core.ws_client import TrueNASClient

    loop = asyncio.get_running_loop()
    client = TrueNASClient(cfg, loop)
    app["truenas_client"] = client
    create_lockfile(cfg)

    # NOTE: This method creates and manages its own background task with
    # asyncio.create_task.
    task = client.start()
    task.add_done_callback(lambda _t: app["shutdown_event"].set())
    app["truenas_task"] = task

    log.info("TrueNAS API websocket client started")


async def truenas_context_manager(app: web.Application):

    # NOTE: This uses the "context manager generator" pattern. It must have
    # exactly one yield, dividing the function in half. The first half
    # is the setup and the second half is the teardown. This convention
    # is set by aiohttp and is required to use app.cleanup_ctx

    from truenas_api_conduit.config import Config, AppBaseConfig  # imports pydantic

    cfg = app["config"]
    log.info(f"cfg type: {cfg.__class__.__name__}")

    if not isinstance(cfg, AppBaseConfig) and not isinstance(cfg, Config):
        raise RuntimeError(f"Config object is not valid: {cfg.__class__.__name__}")

    if cfg.log_level not in ("trace", "debug"):
        # The CLI only has timestamps for debug or trace but the service should
        # always have timestamps
        if app_globals.app_env == core.AppEnv.STANDALONE:
            log_setup.enable_timestamps()
        # for service and docker modes, they'll have their own timestamps

    # NOTE: Requests will check if this is None, if so this will be used
    # as the indicator that the app is in locked mode
    app["truenas_client"] = None

    if isinstance(cfg, Config):
        client_startup(cfg, app)
    elif isinstance(cfg, AppBaseConfig):
        log.warning("Starting app in locked mode")
        # We do nothing else here.

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
        if result := core.delete_lockfile():
            log.error("Failed to delete stale lockfile: %s", result)

        client = app["truenas_client"]
        if client:
            close_result = await client.close()
            log.debug(close_result)
            if close_result.is_closed:
                log.info("The TrueNAS websocket client closed itself gracefully")
            else:
                log.warning(close_result.msg)
        else:
            log.warning("There's no TrueNAS websocket client to close down")


async def main(cfg: Config | AppBaseConfig) -> None:

    log.info("Starting to initialize the HTTP server")

    app = web.Application()
    app["config"] = cfg

    shutdown_event = asyncio.Event()
    app["shutdown_event"] = shutdown_event

    app["unlocker"] = Unlocker(app)

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

    # manages the lifecycle
    app.cleanup_ctx.append(truenas_context_manager)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=cfg.service_address, port=cfg.socket_port)
    await site.start()

    log.info("HTTP server started")

    try:
        await shutdown_event.wait()
    except (GracefulExit, asyncio.CancelledError) as e:
        log.debug("Received exit command (%s)", e.__class__.__name__)
    else:
        log.debug("TrueNAS client task finished with no error")
    finally:
        # will run the teardown in truenas_context_manager:
        await runner.cleanup()


def error_handler(err_string: str, log_level: str, e: BaseException):

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
    from truenas_api_conduit.config import Config, AppBaseConfig

    raw: str | None = None
    cfg: AppBaseConfig | Config
    try:
        if not sys.stdin.isatty():  # piped start, OS startup, etc
            log.info("Detected not a TTY, checking stdin...")
            raw = sys.stdin.read()

        if raw:
            log.info("Detected input on stdin, loading from stdin")
            cfg = Config.model_validate_json(raw)
        else:
            log.info("No input on stdin, loading normally")
            #! cfg = Config()

            cfg = AppBaseConfig()
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

    if isinstance(cfg, Config):
        log.debug("Config: %s", cfg)
        log.debug("Config provenance: %s", cfg.provenance)
    elif isinstance(cfg, AppBaseConfig):
        log.debug("Base Config: %s", cfg)
    else:
        assert_never(cfg)

    if app_env_str := os.environ.get("TRUENAS_APP_ENV"):
        try:
            appenv_enum = core.AppEnv(app_env_str)
        except ValueError:
            if log_level <= level_mapping["TRACE"]:
                raise
            else:
                log.error(
                    "TRUENAS_APP_ENV Environment variable is not valid: %s",
                    app_env_str,
                )
                sys.exit(1)
        else:
            log.info("Detected TRUENAS_APP_ENV: %s", appenv_enum.value)
    else:
        # If the env var is not set it probably means the user ran the
        # truenas-api-conduit entrypoint directly.
        appenv_enum = core.AppEnv.STANDALONE

        if log_level <= level_mapping["TRACE"]:
            raise ValueError("TRUENAS_APP_ENV environment variable is not set")
        else:
            log.error("TRUENAS_APP_ENV environment variable is not set")
            sys.exit(1)

    log.debug("Setting app env to: %s", appenv_enum)
    app_globals.set_app_env(appenv_enum)
    log.debug("App env set to: %s", app_globals.app_env)
    if app_globals.app_env is None:
        raise ValueError("Tried running app with no app_env set")

    # try:
    asyncio.run(
        main(cfg), debug=(level_name.lower() == "trace"), loop_factory=loop_factory
    )
    # except OSError as e:
    #     #! im not 100% sure this is necessary here
    #     # If we got an OSError or other exception at this point then either
    #     # we're in traceback mode, or something is very wrong.
    #     err_string = core.examine_os_error(e)
    #     error_handler(err_string, level_name, e)
    # except Exception as e:
    #     error_handler(str(e), logging.getLevelName(log_level), e)
    # finally:
    #     log.info("Program shutting down now")


if __name__ == "__main__":
    start()
