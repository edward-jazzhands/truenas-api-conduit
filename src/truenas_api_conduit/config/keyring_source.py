# standard library
from typing import Any, Mapping
import logging

# third party
from pydantic import SecretStr
from pydantic.fields import FieldInfo, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
import keyring
import keyring.backend

log = logging.getLogger(__name__)


# Secrets chain example on Linux
# App
#   -> keyring (cross-platform abstraction)
#     -> secretstorage (Linux Secret Service client)
#       -> jeepney (D-Bus transport)
#         -> [GNOME Keyring daemon / KWallet / KeePassXC]


def KeyringField(*args, **kwargs) -> SecretStr:
    "Note this MUST be assigned to a SecretStr field"

    extra: dict = {"keyring": True}
    if caller_extra := kwargs.pop("json_schema_extra", None):
        if not isinstance(caller_extra, dict):
            raise ValueError("KeyringField does not support callable json_schema_extra")
        extra = {**extra, **caller_extra}
    return Field(*args, json_schema_extra=extra, **kwargs)


class KeyringSettingsSource(PydanticBaseSettingsSource):
    """A Pydantic-Settings source class that looks up secrets in the keyring.
    Can be used in a chain with other source classes as intended by pydantic-settings.

    Pass in args to the constructor like you would with other sources in the
    pydantic-settings chain (the settings_customise_sources method). More details
    in the __init__ docstring.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        service: str | None = None,
        keyring_map: Mapping[str, Mapping] | None = None,
        skip: bool = False,
    ) -> None:
        """Initialize the KeyringSettingsSource. Optionally pass in a keyring map.

        NOTE if you pass in a keyring map, the service field will be ignored. If you
        don't pass in a keyring map, service MUST be set.

        If you don't pass in a keyring map, it will use the provided service arg for
        `service` and the field name of each keyring field for `username`.

        Keyring fields you want to use must use the KeyringField function to
        mark them as fields you want to pull from the keyring. For example:

        ```
        api_key_1: str = KeyringField(default=...)
        api_key_2: str = KeyringField(default=...)
        ```

        KeyringField is a helper function that simply does this under the hood:
        ```
        api_key_1: str = Field(default=..., json_schema_extra={"keyring": True})
        ```

        Args:
            settings_cls: The pydantic settings class to use.
            service: The keyring service to use.
            keyring_map: A mapping of field names in your pydantic settings class to keyring
                service and username.
            skip: If True the keyring source will be skipped entirely. This is useful if
                the field was already passed in from a higher priority source (ie CLI args)
                and you don't want to trigger the user's password prompt (pydantic-settings
                evaluates all sources and THEN combines them, they have no knowledge
                of each other)

        Using a keyring map allows you to override the default keyring service and username
        fields if necessary. This might be necessary if the secrets were added manually or
        by another program, you need numerous service names, or if you just have a specific
        shema you want to use.

        The keyring_map dict should look like the following. The outer keys must match
        field names that exist in your pydantic settings class. The inner keys must
        be 'service' and 'username'. That's hard-coded by the keyring library itself.

        ```
        keyring_map: dict[str, dict[str, str]] = {
            "api_key": {"service": "service-1", "username": "api-key"},
            "api_key2": {"service": "service-1", "username": "api-key2"},
            "api_key3": {"service": "service-2", "username": "api-key"},
        }
        ```
        """
        if service is None and keyring_map is None:
            raise ValueError(
                "You must pass in either a service or keyring_map to the "
                "KeyringSettingsSource constructor."
            )

        self.service = service
        self.config_keyring_map = keyring_map
        "keys match field names in the settings class"

        self.skip = skip
        super().__init__(settings_cls)  # available at self.settings_cls

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:

        # This will look for a flag set in the json_schema_extra dict, and pass
        # any fields that don't have it set. Add it to a field by doing this:
        # ```
        # api_key: str = Field(default=..., json_schema_extra={"keyring": True})
        # ```
        # Or using the KeyringField function:
        # ```
        # api_key: str = KeyringField(default=...)
        # ```

        if not self.is_keyring_field(field):
            return None, field_name, False

        # NOTE: If the user has set an alias or validation alias, we want
        # to respect that instead of using the field name
        lookup_key = field_name
        if isinstance(field.validation_alias, str):
            lookup_key = field.validation_alias
        elif isinstance(field.alias, str):
            lookup_key = field.alias

        # If there's a mapping, use it, otherwise use self.service + lookup_key
        if self.config_keyring_map is not None:
            try:
                mapping = self.config_keyring_map[lookup_key]
            except KeyError:
                raise ValueError(
                    f"The keyring map does not contain a mapping for {lookup_key}"
                )
            service = mapping["service"]
            username = mapping["username"]
        else:
            assert_msg = (
                "self.service is None and self.config_keyring_map is also None, "
                "this is supposed to be logically impossible."
            )
            # self.service is guaranteed to not be None if we've reached this point.
            assert self.service is not None, assert_msg
            service = self.service
            username = lookup_key

        # TODO: get_password should maybe run in a different thread?
        # it might be IO bound
        try:
            password = keyring.get_password(service, username)
        except keyring.backend.errors.NoKeyringError:
            log.info(
                "No keyring backend or secrets manager found. You can still pass in your "
                "API key as an environment variable, a CLI option, or in the config file."
            )
        except keyring.backend.errors.KeyringError as e:
            # NOTE: This should not happen simply because the password was not found.
            # This should only happen if there's some kind of bug somewhere.
            log.error(f"Could not get password from keyring: {e}")
            raise
        except Exception as e:
            log.error(f"Unexpected error while trying to get password from keyring: {e}")
            raise
        else:
            # Under normal circumstances, keyring.get_password() will just return
            # None instead of raising an exception. So this should be the typical
            # hot path unless there's a bug somewhere.
            if password is not None:
                log.info("Found API key in keyring")
                return password, field_name, False
            else:
                log.debug("No API key found in keyring")

        # Returns a tuple of (value, field_name, value_is_complex).
        return None, field_name, False

    @staticmethod
    def is_keyring_field(field_info: FieldInfo) -> bool:

        if field_info.json_schema_extra is None or callable(field_info.json_schema_extra):
            return False

        # type checkers already know this is a dict through type narrowing so
        # this is for our own sanity:
        if not isinstance(field_info.json_schema_extra, dict):
            raise RuntimeError("FieldInfo.json_schema_extra must be a dict")

        # We also need to ensure the 'keyring' key exists and is set to True
        # (specifically True, and not merely truthy)
        return field_info.json_schema_extra.get("keyring") is True

    def __call__(self) -> dict[str, Any]:

        if self.skip:
            return {}

        # all_keyrings will always contain 'fail Keyring' and 'chainer ChainerBackend'
        # even if no other keyrings are present.
        all_keyrings = keyring.backend.get_all_keyring()
        log.debug(f"keyring backends: {[k.name for k in all_keyrings]}")

        current_backend = keyring.get_keyring()
        log.debug(f"Current keyring backend: {current_backend.name}")

        return_dict: dict[str, Any] = {}

        for field_name, field_info in self.settings_cls.model_fields.items():
            value, field_name, _value_is_complex = self.get_field_value(
                field=field_info, field_name=field_name
            )
            if value is not None:
                return_dict[field_name] = value

        # NOTE: Recall how this works: any key/value pairs in this dictionary will
        # override the defaults in the Config class. It only needs to contain the
        # specific k/v pairs we want this source to override. The dictionary is
        # then passed to the next source in the chain. This makes it compatible
        # with the pydantic_settings sources chain.
        return return_dict
