# standard library
import json
import os
import sys
import logging
import asyncio
import signal
import tomllib
from functools import partial
from typing import TYPE_CHECKING, Callable, Any
from asyncio import AbstractEventLoop

if TYPE_CHECKING:
    # This module will look at app_globals.is_config_frozen to determine if
    # the config is frozen. As such we need to defer importing it until
    # we've had a chance to set that global.
    from truenas_api_conduit.config import Config, AppBaseConfig
    from truenas_api_conduit.core.ws_client import TrueNASClient


# third party
import pydantic
from aiohttp import web

# project
from truenas_api_conduit import LOCK_FILE
import truenas_api_conduit.core as core
from truenas_api_conduit.app_globals import app_globals
from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.log_setup import logging_manager
import truenas_api_conduit.core.endpoints as endpoints

# setting service=True makes all logs to go stdout instead of stderr
logging_manager.init_logging(service=True)
log = logging.getLogger(__name__)


class Unlocker:

    def __init__(self, app: web.Application) -> None:
        self.app = app

    async def unlock_dict(
        self, json_dict: dict[str, Any] | None = None
    ) -> bool | Exception:
        return self._unlock(json_dict=json_dict)

    async def unlock_key(self, crypt_key: str) -> bool | Exception:
        return self._unlock(crypt_key=crypt_key)

    async def unlock(self) -> bool | Exception:
        return self._unlock()

    def _unlock(
        self, crypt_key: str | None = None, json_dict: dict[str, Any] | None = None
    ) -> bool | Exception:

        log_level = logging.getLogger().level
        log_mapping = logging.getLevelNamesMapping()

        from truenas_api_conduit.config import Config
        from truenas_api_conduit.config.file_encrypter import (
            PasswordGetError,
            GetErrorEnum,
        )

        try:
            # * If not standone then pydantic loads everything from sources.

            # * If the user ran standalone and not locked, their whole config should
            # have been passed in to stdin, which will be pre-validated on their end
            # and already contain the API key. So we load straight into the model.
            #! confirm that here?
            if json_dict:
                cfg = Config(**json_dict)
                # NOTE: CANNOT use model_validate here! That would make it bypass
                # the hooks in settings_customize_sources.

            # * If the user ran standone AND locked, their config will be stored
            # in the app["json_dict"] attribute but we don't want to load it into
            # the model until the unlock password is provided.
            # Used when the crypt key is sent to the /unlock endpoint
            #! We should be able to pass in data from the user's config using
            #! the standalone mode, start locked, storing that data in the app["json_dict"]
            #! attribute, and then only load it all after unlocking.
            # * Make sure this works with the docker version as well.
            elif crypt_key:
                if self.app["json_dict"]:
                    stored_json = self.app["json_dict"]
                    stored_json["crypt_key"] = pydantic.SecretStr(crypt_key)
                    cfg = Config(**stored_json)
                    self.app["json_dict"] = None
                else:
                    cfg = Config(crypt_key=pydantic.SecretStr(crypt_key))
            # normal start, either there's no password set or the password is
            # stored on the server (.cryptkey file or TRUENAS_CRYPT_KEY env var)
            else:
                cfg = Config()
        except pydantic.ValidationError as e:
            return e
        except tomllib.TOMLDecodeError as e:
            return e
        except PasswordGetError as e:
            # This is my custom error class so it will only happen if keyring tried
            # to use my fallback FileEncrypter backend, and the user password was
            # incorrect. Or a bug happened.
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
                        "Unexpected error: %s | Raise the verbosity to see more information",
                        e,
                    )
                    self.app["shutdown_event"].set()
                    return e
        except Exception as e:
            if log_level <= log_mapping["TRACE"]:
                raise
            else:
                err_string = (
                    "Could not initialize config:\n\n"
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
        "address": cfg.conduit_host,  # these 2 cfg items are both in AppBaseConfig
        "header": cfg.request_header,
        "app_env": str(app_globals.app_env.value),
    }

    with open(LOCK_FILE, "w") as f:
        f.write(json.dumps(cfg_dict, indent=2))

    # windows ACLs are a pain and would require an entire third party library
    # just for this purpose. So windows users just get slightly shittier security.
    # Thats the way she goes bubs.
    LOCK_FILE.chmod(0o600)  # HACK: This won't do anything on windows.


def client_closed(_task: asyncio.Task, app: web.Application) -> None:

    log.info("TrueNAS websocket client was closed")

    if app["locked"] is True:
        log.warning("Going into locked mode")
        app.pop("truenas_client", None)
        app.pop("truenas_task", None)
    else:
        app["shutdown_event"].set()


# This is only run by the unlocker
def client_startup(cfg: Config, app: web.Application):

    log.info("Starting TrueNAS API websocket client")

    from truenas_api_conduit.core.ws_client import TrueNASClient

    loop = asyncio.get_running_loop()
    client = TrueNASClient(cfg, loop)
    app["truenas_client"] = client
    app["config"] = cfg

    # NOTE: This method creates and manages its own background task with
    # asyncio.create_task.
    task = client.start()

    callback_partial = partial(client_closed, app=app)
    task.add_done_callback(callback_partial)
    app["truenas_task"] = task
    app["locked"] = False

    log.info("TrueNAS API websocket client started")


async def truenas_context_manager(app: web.Application):

    # NOTE: This uses the "context manager generator" pattern. It must have
    # exactly one yield, dividing the function in half. The first half
    # is the setup and the second half is the teardown. This convention
    # is set by aiohttp and is required to use app.cleanup_ctx

    from truenas_api_conduit.config import AppBaseConfig  # imports pydantic

    cfg = app["config"]
    log.info(f"cfg type: {cfg.__class__.__name__}")

    unlocker: Unlocker = app["unlocker"]
    create_lockfile(cfg)

    if not isinstance(cfg, AppBaseConfig):
        raise RuntimeError(f"Config object is not valid: {cfg.__class__.__name__}")

    if (
        cfg.log_level not in ("trace", "debug")
        and app_globals.app_env == core.AppEnv.STANDALONE
    ):
        # The CLI only has timestamps for debug or trace, but the service should
        # always have timestamps when running in standalone mode. In OS mode
        # or docker, the OS/docker will handle timestamps
        logging_manager.enable_timestamps()

    # NOTE: Requests will check if this is None, if so this will be used
    # as the indicator that the app is in locked mode
    app["truenas_client"] = None

    log.info("cfg.start_locked: %s", cfg.start_locked)
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
            log.error("Unlock attempt failed!: %s", unlock_result)
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
        if result := core.delete_lockfile():
            log.error("Failed to delete lockfile: %s", result)

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
                log.critical("Malformed config JSON: %s", e)
                sys.exit(1)
        else:
            log.debug("No input on stdin")

    try:
        cfg = AppBaseConfig()
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

    log.info("Base Config: %s", cfg)

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
        # truenas-api-conduitd entrypoint directly. We can just set it
        # for them and continue
        appenv_enum = core.AppEnv.STANDALONE

    log.debug("Setting app env to: %s", appenv_enum)
    app_globals.set_app_env(appenv_enum)
    log.debug("App env set to: %s", app_globals.app_env)
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
        err_string = core.examine_os_error(e)
        error_handler(err_string, level_name, e)
    except Exception as e:
        error_handler(str(e), logging.getLevelName(log_level), e)
    finally:
        log.warning("Program shutting down now")


if __name__ == "__main__":
    start()
