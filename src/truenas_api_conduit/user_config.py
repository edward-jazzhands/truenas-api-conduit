# standard library
import sys
import shutil
import os
from pathlib import Path
import tomllib
from typing import Any, Final
import logging

# third party
import keyring
import pydantic
from pydantic import BaseModel, field_validator, Field, computed_field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
    SecretsSettingsSource,
    CliSettingsSource,
)

# project
from truenas_api_conduit import APP_NAME

log = logging.getLogger(__name__)

# NOTE: It does not make sense to use platformdirs here because the config file
# must be edited manually by the user. On Windows and MacOS, the conventional
# app data directories are hidden from users by default, so average users
# wouldn't be able to find the config file (these locations are intended for
# programs that manage their own data internally).
# Since we need the user to edit the config file, for Windows and MacOS we
# place the config folder directly in the home directory. This is considered
# standard practice for cross-platform apps with a user-editable config file.
# For Linux we follow the XDG Base Directory specification instead.
if sys.platform == "linux":
    config_dir = Path.home() / ".config" / APP_NAME
    log.debug("Detected Linux")
else:
    config_dir = Path.home() / APP_NAME
    if sys.platform == "win32":
        log.debug("Detected Windows")
    elif sys.platform == "darwin":
        log.debug("Detected MacOS")
    else:
        log.debug("Unknown Operating System")

CONFIG_PATH: Final = config_dir / "config.toml"


def setup_user_config_folder() -> None:
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.error("Could not create the config directory. Aborting - See traceback.")
        raise
    
    if not CONFIG_PATH.exists():
        settings_file_path = Path(__file__).parent / "settings.conf"
        try:
            shutil.copy(settings_file_path, CONFIG_PATH)
            # OR:
            # settings_file.copy(CONFIG_PATH) # This is the newer way but its only 3.14+
        except Exception as e:
            log.error(f"Could not create the default config file: {e}")


# Secrets chain example on Linux
# App
#   -> keyring (cross-platform abstraction)
#     -> secretstorage (Linux Secret Service client)
#       -> jeepney (D-Bus transport)
#         -> [GNOME Keyring daemon / KWallet / KeePassXC]

class KeyringSettingsSource(PydanticBaseSettingsSource):
    
    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)

        # Get the defaults from the main Config class:
        self.service_field: str = Config.secrets_manager_service_field
        self.username_field: str = Config.secrets_manager_username_field

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        super().get_field_value(field, field_name)
        # Required override from PydanticBaseSettingsSource (an ABC).
        # Returns a tuple of (value, field_name, value_is_complex).

        # NOTE: We don't actually need to use this method so this is a placeholder.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:

        # This will automatically read from the toml file set in the model_config
        toml_source = TomlConfigSettingsSource(self.settings_cls)
        toml_data = toml_source()

        log.debug("Parsed toml data")

        service_field = toml_data.get("secrets_manager_service_field")
        username_field = toml_data.get("secrets_manager_username_field")
        log.debug(f"service_field: {service_field} | username_field: {username_field}")

        if service_field is not None:
            self.service_field = service_field
        if username_field is not None:
            self.username_field = username_field

        password = keyring.get_password(self.service_field, self.username_field)

        d: dict[str, Any] = {}
        if password is not None:
            log.debug("Found API key in keyring")
            d["api_key"] = password
        else:
            log.debug("No API key found in keyring")
        return d


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        env_file_encoding="utf-8",
        env_prefix="TRUENAS_",
        frozen=False,
        validate_by_name=True,    # <- This is the new and recommended way
        validate_by_alias=True,
        # populate_by_name=True, # ! Not recommended in v2.11 and above
    )

    # NOTE: pydantic-settings sources docs:
    # https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/#other-settings-source

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:

        # Priority follows the order of the tuple:
        return (
            init_settings, #                         1. CLI flags
            KeyringSettingsSource(settings_cls), #   2. Keyring/Secrets Manager
            env_settings, #                          3. Environment variables   
            TomlConfigSettingsSource(settings_cls) # 4. Config file
            # Our Config class itself becomes lowest 5. Config class defaults
        )

    # NOTE: Because we have env_settings in the sources, Pydantic will look for
    # env variables with the same name as each field, with the env_prefix="TRUENAS_".
    # `api_key` would be TRUENAS_API_KEY, `log_level` would be TRUENAS_LOG_LEVE, etc.
    # The one exception is `truenas_host`, which will use the alias "TRUENAS_HOST".

    # User settings
    truenas_host: str = Field(validation_alias="TRUENAS_HOST")
    secrets_manager_service_field: str = "truenas"
    secrets_manager_username_field: str = "api-key"
    api_key: str
    api_route: str = "/api/current"
    log_level: str = "warning"
    rich_traceback: bool = False

    # NOTE: computed_field decorator docs:
    # https://pydantic.dev/docs/validation/latest/concepts/fields/#the-computed_field-decorator

    # Internal settings
    @computed_field
    @property
    def uri(self) -> str:
        return f"wss://{self.truenas_host}{self.api_route}"

    # NOTE: field_validator decorator docs:
    # https://pydantic.dev/docs/validation/latest/concepts/validators/#json-schema-and-field-validators

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"debug", "info", "warning", "error", "critical"}
        if v.lower() not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return v.lower()

    # @field_validator("local_storage_path", mode="before")
    # @classmethod
    # def expand_storage_path(cls, v: Any) -> Path:
    #     return Path(v).expanduser()


# <>-<> ABOUT PYDANTIC ERRORS <>-<>

# Pydantic contains an `errors()` method that returns a list of errors
# that were encountered during validation. This is a list of
# `ErrorDetails` objects, which are a dict with the following keys:

# - `type`: The type of error that occurred, machine-readable
# - `loc`: tuple of (str, int) identifying where in the schema the error occurred.
#   the str is the name of the field (the key), and the int is {???}
# - `msg`: A human readable error message.
# - `input`: The input data at this `loc` that caused the error.
# - `ctx`: Values which are required to render the error message, and could hence be useful in
#   rendering custom error messages. Also useful for passing custom error data forward.
# - `url`: The documentation URL giving information about the error. No URL is available if
#   a [`PydanticCustomError`][pydantic_core.PydanticCustomError] is used.

# ABOUT 'loc'

# The loc tuple represents the path to the field that failed validation, tracing through
# your nested data structure from the root down to the exact problem location. Each
# element in the tuple is one step deeper into the nesting. For simple fields, it's just
# the field name like ('username',). For nested models, it shows the path through field
# names like ('username', 'address', 'zip_code'). When validating sequences like lists,
# an integer index appears in the path to indicate which element failed, like
# ('items', 2, 'price') for an error in the third item's price field.

# <>-<> Some common scenarios <>-<>

#* "Required field not found"
# You get type: "missing", msg is something like "Field required".

#* "Field not in the model"
# By default pydantic-settings just silently ignores extra fields. If you want 
# it to error, you need model_config = SettingsConfigDict(extra="forbid"), 
# then you'll get type: "extra_forbidden".

#* "Field is empty" (e.g. host = "")
# This changes depending on the type. A str field will accept "" with no error. If  
# you want to reject empty strings you need to add a validator like min_length=1 
# or a @field_validator. A None value on a non-optional field gives you 
# type: "missing" or type: "none_required" depending on context.

#* "Field is invalid" (e.g. port = "banana" for an int field)
# You get type: "int_parsing" or similar, and msg like 
# "Input should be a valid integer".

