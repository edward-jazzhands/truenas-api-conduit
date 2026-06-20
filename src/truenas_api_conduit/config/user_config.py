# standard library
from typing import Any, TYPE_CHECKING
from pathlib import Path
import logging

if TYPE_CHECKING:
    from pydantic_settings import PydanticBaseSettingsSource, DotEnvSettingsSource
    from pydantic import (
        FieldSerializationInfo,
    )

# third party
import keyring
from pydantic import (
    field_validator,
    field_serializer,
    Field,
    SecretStr,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    TomlConfigSettingsSource,
    EnvSettingsSource,
    InitSettingsSource,
    SecretsSettingsSource,
)

# project
from truenas_api_conduit import APP_NAME
from truenas_api_conduit.log_setup import logging_manager
from truenas_api_conduit.app_globals import app_globals
from truenas_api_conduit.console import set_no_color
from truenas_api_conduit.core import CONFIG_PATH, ENV
from truenas_api_conduit.config.keyring_source import (
    KeyringSettingsSource,
    KeyringField,
)

__all__ = ["Config", "AppBaseConfig"]

log = logging.getLogger(__name__)

if not CONFIG_PATH.exists():
    log.error("Config file not found, this is a runtime bug.")
    raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")

# i've chosen a plain dict instead of a dataclass or something here
# because it means I don't need to worry about adding/removing fields,
# it just stays 100% dynamic.
_config_provenance: dict[str, Any] = {}


class TrackingSourceMixin:
    source_label: str

    def __call__(self) -> dict[str, Any]:
        # Call intercepter - we're doing a super call, examining the return
        # value, then returning that value as normal.
        log.debug(f"Calling source: {self.source_label}")

        # Recall, every source returns the dict of k/v pairs it provides
        data: dict[str, Any] = super().__call__()  # type: ignore
        for key in data:  #    ^^^^ super in a Mixin follows MRO
            key_lower = key.lower()
            if key_lower not in _config_provenance:
                _config_provenance[key_lower] = self.source_label
                log.debug(f"{key_lower} was loaded from {self.source_label}")
        if data.get("api_key"):
            # This is straight from the source so it would display the API key
            # if we didn't mask it manually here (its not a SecretStr yet)
            data_copy = data.copy()
            data_copy["api_key"] = "*" * 10
            log.debug(data_copy)
        else:
            log.debug(data)
        return data


# we didn't override __init__ so the construtors are the same on
# all the tracking source classes


class TrackingEnvSource(TrackingSourceMixin, EnvSettingsSource):
    source_label = "env"


class TrackingTomlSource(TrackingSourceMixin, TomlConfigSettingsSource):
    source_label = "config file"


class TrackingInitSource(TrackingSourceMixin, InitSettingsSource):
    source_label = "cli"


class TrackingKeyringSource(TrackingSourceMixin, KeyringSettingsSource):
    source_label = "keyring"


class TrackingSecretsSource(TrackingSourceMixin, SecretsSettingsSource):
    source_label = "secrets"


secrets_dir = Path("/run/secrets")


class AppBaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        toml_file=CONFIG_PATH,
        secrets_dir=secrets_dir if secrets_dir.exists() else None,
        env_file_encoding="utf-8",
        env_prefix="TRUENAS_",
        frozen=app_globals.is_config_frozen,
        validate_by_name=True,  # <- This is the new and recommended way
        validate_by_alias=True,
        # populate_by_name=True, # ! Deprecated - Not recommended in v2.11+
    )

    @classmethod
    def settings_customise_sources(  # type: ignore
        cls,
        settings_cls: type[BaseSettings],
        init_settings: InitSettingsSource,  # <- type checkers don't like this but its fine
        env_settings: EnvSettingsSource,  # same here
        dotenv_settings: DotEnvSettingsSource,
        file_secret_settings: SecretsSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:

        # if the API key was passed in from the CLI then we skip the keyring
        init_processed = init_settings()
        skip = False
        if init_processed.get("api_key"):
            log.debug("Found API key in init kwargs. Skipping keyring")
            skip = True
        else:
            # my custom fallback file encrypter keyring backend. This is set to
            # lowest priority (0.0) so that it should only be used if no other
            # keyring backends are available.
            from truenas_api_conduit.config.file_encrypter import FileEncrypter

            keyring.set_keyring(FileEncrypter(init_processed.get("crypt_key")))

        # Priority follows the order of the tuple:
        # 1. CLI flags passed into constructor
        # 2. Environment variables
        # 3. Config file
        # 4. Keyring/Secrets Manager
        # 5. File secrets (mostly for Docker secrets but not exclusively)
        # 6. Config class defaults
        return (
            TrackingInitSource(settings_cls, init_settings.init_kwargs),
            TrackingEnvSource(settings_cls),
            TrackingTomlSource(settings_cls),
            TrackingKeyringSource(settings_cls, service=APP_NAME, skip=skip),
            TrackingSecretsSource(settings_cls),
        )

    # These fields are the ones needed to start up the aiohttp
    # server. This allows it to start up independently of putting in
    # the API key and other server config vars.

    log_level: str = "warning"
    no_color: bool = Field(default=False, validation_alias="NO_COLOR")
    conduit_host: str = "localhost:4567"
    request_header: str | None = None
    stealth_mode: bool = False
    start_locked: bool = False
    truenas_address: str = Field(default=..., validation_alias="TRUENAS_ADDRESS")
    # NOTE: truenas_address was moved here only to give better error messages.
    # Its not needed for the locked state. But it needs to be here for the user
    # to see the error messages regarding it when they start the app in locked mode.

    @property
    def provenance(self) -> dict[str, str]:
        return _config_provenance

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"trace", "debug", "info", "warning", "error", "critical"}
        if v.lower() not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return v.lower()

    @field_validator("conduit_host")
    @classmethod
    def validate_conduit_host(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError("Conduit host address must be in the format of 'host:port'")
        elif v.count(":") > 1:
            raise ValueError("Conduit host address must not contain more than one ':'")
        _host, port = v.split(":")
        int(port)
        return v

    @field_validator("truenas_address", mode="before")
    @classmethod
    def truenas_address_missing(cls, v: str) -> str:
        # NOTE: validators that check if the value has been set at all
        # need to have the before mode in order to show the use our
        # custom ValueError, otherwise it would just show the default
        # pedantic error
        if not v:
            raise ValueError(
                "You need to set a value for your TrueNAS server's address. You can set "
                "it in one of the following ways:\n"
                "  1. In the config file\n"
                f"  2. As an environment variable [env: {ENV['truenas_address']}=]\n"
                "  3. Using the --truenas-address option on the command line."
            )
        return v

    @field_validator("truenas_address")
    @classmethod
    def truenas_address_used_placeholder(cls, v: str) -> str:
        if v == "192.168.1.xxx:443":
            raise ValueError(
                "You need to enter a value for your TrueNAS server's address. The value "
                "which you enabled in the config file is only for demonstration. You can "
                f"also set it as an environment variable ({ENV['truenas_address']}), or using the "
                "--truenas-address option in the CLI (see start --help)."
            )
        return v

    @field_validator("truenas_address")
    @classmethod
    def truenas_address_clean_prefix(cls, v: str) -> str:
        if v.startswith("http://"):
            raise ValueError(
                "You have entered an HTTP address for your TrueNAS server instead of an "
                "HTTPS address. This is strictly forbidden, TrueNAS will delete your API "
                "key if any attempt is made to do this (That's not my doing that's "
                "just how TrueNAS works)."
            )
        if v.startswith("https://"):
            return v.removeprefix("https://")
        return v

    def model_post_init(self, _context: Any) -> None:

        log_mapping = logging.getLevelNamesMapping()
        logging_manager.set_log_level(log_mapping[self.log_level.upper()])
        log.info("Config post init: log_level set to %s", self.log_level)

        if self.no_color:
            log.info("Config post init: Disabling color output")
            set_no_color()

        for field, _value in AppBaseConfig.model_fields.items():
            if field not in self.provenance:
                self.provenance[field] = "default"


class Config(AppBaseConfig):
    model_config = SettingsConfigDict(
        extra="forbid",
        toml_file=CONFIG_PATH,
        secrets_dir=secrets_dir if secrets_dir.exists() else None,
        env_file_encoding="utf-8",
        env_prefix="TRUENAS_",
        frozen=app_globals.is_config_frozen,
        validate_by_name=True,  # <- This is the new and recommended way
        validate_by_alias=True,
        # populate_by_name=True, # ! Deprecated - Not recommended in v2.11+
    )

    # pydantic-settings sources docs:
    # https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/#other-settings-source

    # NOTE: Because we have env_settings in the sources, Pydantic will look for
    # env variables with the same name as each field, with the env_prefix="TRUENAS_".
    # `api_key` would be TRUENAS_API_KEY, `log_level` would be TRUENAS_LOG_LEVEL, etc.
    # The two exceptions are at the top: `truenas_address`, which will use the alias
    # "TRUENAS_ADDRESS", because otherwise it would be TRUENAS_TRUENAS_ADDRESS. The same
    # goes for `truenas_cert_path`.

    # Oh there's also now a third validation alias: "NO_COLOR". This is because
    # Rich-Click also uses that env var to disable color, so we use the same one.
    # I believe its a common convention for CLI programs.

    # using default=... is a way to tell pydantic that the field is required,
    # while also preventing it from being a required constructor argument.
    # It signals to Pyright that Pydantic will take care of the validation.

    # User settings
    truenas_cert_path: str | None = Field(
        default=None, validation_alias="TRUENAS_CERT_PATH"
    )
    validate_certs: bool = True
    api_key: SecretStr = KeyringField(default=...)  # custom field function
    api_route: str = "/api/current"
    crypt_key: SecretStr | None = Field(default=None)

    # Internal settings

    # this is not a computed field because we don't want it to show up
    # in the model dump. It's only used internally.
    @property
    def uri(self) -> str:
        return f"wss://{self.truenas_address}{self.api_route}"

    # field_validator decorator docs:
    # https://pydantic.dev/docs/validation/latest/concepts/validators/#json-schema-and-field-validators

    @field_serializer("api_key")
    def serialize_api_key(self, secret: SecretStr, info: FieldSerializationInfo):
        if info.context and info.context.get("unmask") is True:
            return secret.get_secret_value()
        return secret

    @field_serializer("crypt_key")
    def serialize_crypt_key(self, secret: SecretStr, info: FieldSerializationInfo):
        if (
            info.context
            and info.context.get("unmask") is True
            and self.crypt_key is not None
        ):
            return secret.get_secret_value()
        return secret

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "You need to set a value for your TrueNAS API key. You can set "
                "it in one of the following ways:\n"
                "  1. Using the set-key command in the CLI\n"
                "  2. Using the --api-key option in the CLI (see start --help)."
                f"  3. As an environment variable [env: {ENV['api_key']}=]\n"
                "  4. In the config file (least secure)\n"
            )
        return v

    # FOR REFERENCE: Example expanding a path:
    # @field_validator("local_storage_path", mode="before")
    # @classmethod
    # def expand_storage_path(cls, v: Any) -> Path:
    #     return Path(v).expanduser()

    def model_post_init(self, _context: Any) -> None:

        log_mapping = logging.getLevelNamesMapping()
        logging_manager.set_log_level(log_mapping[self.log_level.upper()])
        log.info("Config post init: log_level set to %s", self.log_level)

        if self.no_color:
            log.info("Config post init: Disabling color output")
            set_no_color()

        for field, _value in Config.model_fields.items():
            if field not in self.provenance:
                self.provenance[field] = "default"
