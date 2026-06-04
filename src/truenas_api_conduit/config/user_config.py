# standard library
from typing import Any
import logging

# third party
from pydantic import field_validator, Field, computed_field
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
from truenas_api_conduit.config.keyring_source import KeyringSettingsSource

__all__ = ["Config"]

log = logging.getLogger(__name__)

if not CONFIG_PATH.exists():
    log.error("Config file not found, this is a runtime bug.")
    raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")


_config_provenance: dict[str, Any] = {}


class TrackingSourceMixin:
    source_label: str

    def __call__(self) -> dict[str, Any]:
        # Recall, every source returns the dict of k/v pairs it provides
        data: dict[str, Any] = super().__call__()  # type: ignore
        for key in data:  #    ^^^^ super in a Mixin follows MRO
            key_lower = key.lower()
            if key_lower not in _config_provenance:
                _config_provenance[key_lower] = self.source_label
                log.debug(f"{key_lower} was loaded from {self.source_label}")
        return data


# Every new tracking source class maintains the constructor signature
# of the original source class because we didn't override __init__


class TrackingEnvSource(TrackingSourceMixin, EnvSettingsSource):
    source_label = "env"


class TrackingTomlSource(TrackingSourceMixin, TomlConfigSettingsSource):
    source_label = "config file"


class TrackingInitSource(TrackingSourceMixin, InitSettingsSource):
    source_label = "cli"


class TrackingKeyringSource(TrackingSourceMixin, KeyringSettingsSource):
    source_label = "keyring"


class SecretStr(str):
    "Will only print the first 10 chars of the secret during logging output"

    def __repr__(self):
        return f"'{self[:10]}...'"

    def __str__(self):
        return f"{self[:10]}..."


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        env_file_encoding="utf-8",
        env_prefix="TRUENAS_",
        frozen=False,
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

        log.info("Populating settings from sources...")

        # Priority follows the order of the tuple:
        # 1. CLI flags passed into constructor
        # 2. Keyring/Secrets Manager
        # 3. Environment variables
        # 4. Config file
        # 5. Config class defaults
        return (
            TrackingInitSource(settings_cls, init_settings.init_kwargs),
            TrackingKeyringSource(
                settings_cls, service="truenas", raise_on_missing_key=False
            ),
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

    # NOTE: using default=... is a way to tell pydantic that the field is required,
    # while also preventing it from being a required constructor argument.
    # It signals to Pyright that Pydantic will take care of the validation.

    # User settings
    truenas_host: str = Field(default=..., validation_alias="TRUENAS_HOST")
    truenas_cert_path: str | None = Field(
        default=None, validation_alias="TRUENAS_CERT_PATH"
    )
    validate_certs: bool = True
    api_key: SecretStr | str = Field(default=..., json_schema_extra={"keyring": True})
    api_route: str = "/api/current"
    log_level: str = "warning"
    no_color: bool = Field(default=False, validation_alias="NO_COLOR")
    socket_port: int = 4567
    service_address: str = "localhost"

    # computed_field decorator docs:
    # https://pydantic.dev/docs/validation/latest/concepts/fields/#the-computed_field-decorator

    # Internal settings

    @property
    def uri(self) -> str:
        return f"wss://{self.truenas_host}{self.api_route}"

    @property
    def provenance(self) -> dict[str, str]:
        return _config_provenance

    # field_validator decorator docs:
    # https://pydantic.dev/docs/validation/latest/concepts/validators/#json-schema-and-field-validators

    @field_validator("api_key", mode="before")
    @classmethod
    def coerce_secret(cls, v):
        return SecretStr(v)

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
                "You need to set a value for your TrueNAS server's address. You can set it in "
                "the config file, as an environment variable (TRUENAS_HOST), or using the "
                "--truenas-host option on the command line."
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
                "--truenas-host option on the command line."
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
                "You need to set a value for your TrueNAS API key. You can set it in "
                "the config file, as an environment variable (TRUENAS_API_KEY), or using the "
                "--api-key option on the command line."
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
        log.debug("Config post init: log_level set to %s", self.log_level)

        if self.no_color:
            log.debug("Config post init: Disabling color output")
            set_no_color()

        for field, value in Config.model_fields.items():
            if field not in self.provenance:
                self.provenance[field] = "default"
