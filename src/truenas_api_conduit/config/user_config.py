# standard library
from typing import Any
import logging

# third party
import keyring
from pydantic import (
    field_validator,
    field_serializer,
    Field,
    SecretStr,
    FieldSerializationInfo,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
    EnvSettingsSource,
    InitSettingsSource,
)

# project
from truenas_api_conduit import log_setup
from truenas_api_conduit.console import set_no_color
from truenas_api_conduit.core import CONFIG_PATH
from truenas_api_conduit.config.pydantic_sources import (
    KeyringSettingsSource,
    KeyringField,
)
from truenas_api_conduit.app_globals import is_config_frozen

__all__ = ["Config"]

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


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        env_file_encoding="utf-8",
        env_prefix="TRUENAS_",
        frozen=is_config_frozen,
        validate_by_name=True,  # <- This is the new and recommended way
        validate_by_alias=True,
        # populate_by_name=True, # ! Deprecated - Not recommended in v2.11+
    )

    # pydantic-settings sources docs:
    # https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/#other-settings-source

    @classmethod
    def settings_customise_sources(  # type: ignore
        cls,
        settings_cls: type[BaseSettings],
        init_settings: InitSettingsSource,  # <- type checkers don't like this but its fine
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:

        # if the API key was passed in from the CLI then we skip the keyring
        skip = False
        api_key = init_settings.init_kwargs.get("api_key")
        if api_key is not None:
            log.debug("Found API key in init kwargs. Skipping keyring")
            skip = True
        else:
            from truenas_api_conduit.config.keyring_backends import FileEncrypter

            # my custom fallback file encrypter keyring backend. This is set to
            # lowest priority (0.0) so that it should only be used if no other
            # keyring backends are available.
            keyring.set_keyring(FileEncrypter())

        # Priority follows the order of the tuple:
        # 1. CLI flags passed into constructor
        # 2. Keyring/Secrets Manager
        # 3. Environment variables
        # 4. Config file
        # 5. Config class defaults
        return (
            TrackingInitSource(settings_cls, init_settings.init_kwargs),
            TrackingKeyringSource(settings_cls, service="truenas-api-conduit", skip=skip),
            TrackingEnvSource(settings_cls),
            TrackingTomlSource(settings_cls),
        )

    # NOTE: Because we have env_settings in the sources, Pydantic will look for
    # env variables with the same name as each field, with the env_prefix="TRUENAS_".
    # `api_key` would be TRUENAS_API_KEY, `log_level` would be TRUENAS_LOG_LEVE, etc.
    # The two exceptions are at the top: `truenas_host`, which will use the alias
    # "TRUENAS_HOST", because otherwise it would be TRUENAS_TRUENAS_HOST. The same
    # goes for `truenas_cert_path`.

    # Oh there's also now a third validation alias: "NO_COLOR". This is because
    # Rich-Click also uses that env var to disable color, so we use the same one.
    # I believe its a common convention for CLI programs.

    # using default=... is a way to tell pydantic that the field is required,
    # while also preventing it from being a required constructor argument.
    # It signals to Pyright that Pydantic will take care of the validation.

    # User settings
    truenas_host: str = Field(default=..., validation_alias="TRUENAS_HOST")
    truenas_cert_path: str | None = Field(
        default=None, validation_alias="TRUENAS_CERT_PATH"
    )
    validate_certs: bool = True
    api_key: SecretStr = KeyringField(default=...)  # custom field function
    api_route: str = "/api/current"
    log_level: str = "warning"
    no_color: bool = Field(default=False, validation_alias="NO_COLOR")
    socket_port: int = 4567
    service_address: str = "localhost"
    request_header: str | None = None

    # Internal settings

    # this is not a computed field because we don't want it to show up
    # in the model dump. It's only used internally.
    @property
    def uri(self) -> str:
        return f"wss://{self.truenas_host}{self.api_route}"

    @property
    def provenance(self) -> dict[str, str]:
        return _config_provenance

    # field_validator decorator docs:
    # https://pydantic.dev/docs/validation/latest/concepts/validators/#json-schema-and-field-validators

    @field_serializer("api_key")
    def serialize_api_key(self, secret: SecretStr, info: FieldSerializationInfo):
        if info.context and info.context.get("unmask") is True:
            return secret.get_secret_value()
        return secret

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"trace", "debug", "info", "warning", "error", "critical"}
        if v.lower() not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return v.lower()

    @field_validator("truenas_host", mode="before")
    @classmethod
    def truenas_host_missing(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "You need to set a value for your TrueNAS server's address. You can set "
                "it in one of the following ways:\n"
                "  1. In the config file\n"
                "  2. As an environment variable [env: TRUENAS_HOST=]\n"
                "  3. Using the --truenas-host option on the command line."
            )
        return v

    @field_validator("truenas_host", mode="after")
    @classmethod
    def truenas_host_used_placeholder(cls, v: str) -> str:
        if v == "192.168.1.xxx:443":
            raise ValueError(
                "You need to enter a value for your TrueNAS server's address. The value "
                "which you enabled in the config file is only for demonstration. You can "
                "also set it as an environment variable (TRUENAS_HOST), or using the "
                "--truenas-host option in the CLI (see start --help)."
            )
        return v

    @field_validator("truenas_host", mode="after")
    @classmethod
    def truenas_host_clean_prefix(cls, v: str) -> str:
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

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "You need to set a value for your TrueNAS API key. You can set "
                "it in one of the following ways:\n"
                "  1. Using the set-key command in the CLI\n"
                "  2. Using the --api-key option in the CLI (see start --help)."
                "  3. As an environment variable [env: TRUENAS_API_KEY=]\n"
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
        log_setup.set_log_level(log_mapping[self.log_level.upper()])
        log.info("Config post init: log_level set to %s", self.log_level)

        if self.no_color:
            log.info("Config post init: Disabling color output")
            set_no_color()

        for field, value in Config.model_fields.items():
            if field not in self.provenance:
                self.provenance[field] = "default"
