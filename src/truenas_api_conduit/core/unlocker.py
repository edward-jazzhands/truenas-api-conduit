# standard library
import logging
import tomllib
from functools import partial
import asyncio
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from truenas_api_conduit.config import Config

log = logging.getLogger(__name__)

_all__ = ["Unlocker"]


# This is only run by the unlocker
def _client_startup(cfg: Config, app: web.Application):

    log.info("Starting TrueNAS API websocket client")

    from truenas_api_conduit.core.ws_client import TrueNASClient

    loop = asyncio.get_running_loop()
    client = TrueNASClient(cfg, loop)
    app["truenas_client"] = client
    app["config"] = cfg

    # NOTE: This method creates and manages its own background task with
    # asyncio.create_task.
    task = client.start()

    callback_partial = partial(_client_closed, app=app)
    task.add_done_callback(callback_partial)
    app["truenas_task"] = task
    app["locked"] = False

    log.info("TrueNAS API websocket client started")


# only used/set up by the client_startup function
def _client_closed(_task: asyncio.Task, app: web.Application) -> None:

    log.info("TrueNAS websocket client was closed")

    if app["locked"] is True:
        log.warning("Going into locked mode")
        app.pop("truenas_client", None)
        app.pop("truenas_task", None)
    else:
        app["shutdown_event"].set()


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

        import pydantic
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

            _client_startup(cfg, self.app)
            return True
