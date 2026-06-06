# standard library
from typing import Any, Mapping, assert_never
import logging
import sys

# third party
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from keyring.errors import KeyringError

# project
from truenas_api_conduit.errors import ProgrammerError, ConduitError
from truenas_api_conduit.console import console_stderr

log = logging.getLogger(__name__)


# Secrets chain example on Linux
# App
#   -> keyring (cross-platform abstraction)
#     -> secretstorage (Linux Secret Service client)
#       -> jeepney (D-Bus transport)
#         -> [GNOME Keyring daemon / KWallet / KeePassXC]


class KeyringLookupError(ConduitError, KeyringError, LookupError):
    def __init__(self, service: str, username: str):
        self.service = service
        self.username = username
        super().__init__(
            f"No results found in keyring for service: '{service}' and username: {username}\n"
            "This is raising an exception because `raise_on_missing_key` is True "
            "when initialzing the KeyringSettingsSource class. Change it to False to "
            "suppress this error."
        )


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
    ) -> None:
        """Initialize the KeyringSettingsSource. Optionally pass in a keyring map.

        NOTE if you pass in a keyring map, the service field will be ignored. If you
        don't pass in a keyring map, service MUST be set.

        If you don't pass in a keyring map, it will use the provided service arg for
        `service` and the field name of each keyring field for `username`.

        Keyring fields you want to use must pass in a dict to the json_schema_extra
        arg containing {"keyring": True}. For example:

        ```
        api_key_1: str = Field(default=..., json_schema_extra={"keyring": True})
        api_key_2: str = Field(default=..., json_schema_extra={"keyring": True})
        ```

        Args:
            settings_cls: The pydantic settings class to use.
            service: The keyring service to use.
            keyring_map: A mapping of field names in your pydantic settings class to keyring
                service and username.
            raise_on_missing_key: Whether to raise an error if a key is not found in the keyring.

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
            raise ProgrammerError(
                "You must pass in either a service or keyring_map to the "
                "KeyringSettingsSource constructor."
            )

        self.service = service
        self.config_keyring_map = keyring_map
        "keys match field names in the settings class"

        super().__init__(settings_cls)  # available at self.settings_cls

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Required override from PydanticBaseSettingsSource (an ABC).
        # Returns a tuple of (value, field_name, value_is_complex).

        # NOTE: We don't actually need to use this method so this is a placeholder.
        # The PydanticBaseSettingsSource is designed so that you call this method
        # yourself in the __call__ override. But, this isn't used by anything else,
        # and it's not actually necessary to use it at all. So I'm just not.
        # Modern problems require modern solutions.

        return None, field_name, False

    @staticmethod
    def is_keyring_field(field_info: FieldInfo) -> bool:

        if field_info.json_schema_extra is None or callable(field_info.json_schema_extra):
            return False

        # type checkers already know this is a dict through type narrowing so
        # this is for our own sanity:
        assert isinstance(field_info.json_schema_extra, dict)

        # We also need to ensure the 'keyring' key exists and is set to True
        # (specifically True, and not merely truthy)
        return field_info.json_schema_extra.get("keyring") is True

    def __call__(self) -> dict[str, Any]:

        # This is only called one time when the program launches, so it makes
        # sense to put a lazy import here to improve startup time.
        import keyring
        import keyring.backend
        from truenas_api_conduit.config.keyring_backends import (
            FileEncrypter,
            PasswordGetError,
            GetErrorEnum,
        )

        # my custom fallback file encrypter keyring backend. This is set to
        # lowest priority (0.0) so that it should only be used if no other
        # keyring backends are available.
        keyring.set_keyring(FileEncrypter())

        # all_keyrings will always contain 'fail Keyring' and 'chainer ChainerBackend'
        # even if no other keyrings are present.
        all_keyrings = keyring.backend.get_all_keyring()
        log.debug(f"keyring backends: {[k.name for k in all_keyrings]}")

        current_backend = keyring.get_keyring()
        log.debug(f"Current keyring backend: {current_backend.name}")

        return_dict: dict[str, Any] = {}

        for field_name, field_info in self.settings_cls.model_fields.items():

            # This will look for a flag set in the json_schema_extra dict, and pass
            # any fields that don't have it set. Add it to a field by doing this:
            # ```
            # api_key: str = Field(default=..., json_schema_extra={"keyring": True})
            # ```

            if not self.is_keyring_field(field_info):
                continue

            # NOTE: If the user has set an alias or validation alias, we want
            # to respect that instead of using the field name
            lookup_key = field_name
            if isinstance(field_info.validation_alias, str):
                lookup_key = field_info.validation_alias
            elif isinstance(field_info.alias, str):
                lookup_key = field_info.alias

            # If there's a mapping, use it, otherwise use self.service + lookup_key
            if self.config_keyring_map is not None:
                try:
                    mapping = self.config_keyring_map[lookup_key]
                except KeyError:
                    raise ProgrammerError(
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

            # TODO: get_password should maybe run in a different thread,
            # it might be IO bound
            try:
                password = keyring.get_password(service, username)
            except keyring.backend.errors.NoKeyringError:
                log.info(
                    "No keyring backend or secrets manager found. You can still pass in your "
                    "API key as an environment variable, a CLI option, or in the config file."
                )
                return {}
            except PasswordGetError as e:
                # This is my custom error class so it will only happen if keyring tried
                # to use my fallback FileEncrypter backend, and the user password was
                # not found.
                if e.err_code == GetErrorEnum.NOT_A_TTY:
                    log.warning(
                        "TRUENAS_CRYPT_KEY environment variable not set and stdin is not "
                        "a TTY. There's no way to use the FileEncrypter keyring backend."
                    )
                    pass
                elif e.err_code == GetErrorEnum.VAULT_FILE_NOT_FOUND:
                    log.debug("No vault file found for: %s.%s", service, username)
                    pass
                elif e.err_code == GetErrorEnum.SALT_FILE_NOT_FOUND:
                    console_stderr.print(
                        f"No salt file found for: {service}.{username} -- the key is not "
                        "retrievable. Please delete the key and set it again."
                        "\nDo you want to continue, or exit the program?",
                    )
                    answer = input("Enter 'y' to continue. Anything else will exit:  ")
                    if answer.lower() == "y":
                        continue
                    else:
                        sys.exit(1)
                elif e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
                    console_stderr.print(
                        "The encryption key you have entered is incorrect. The program "
                        "will fall back to reading the API key from a different source. "
                        "Otherwise you must exit and restart to enter your encryption "
                        "key again. \nDo you want to continue, or exit the program?"
                    )
                    answer = input("Enter 'y' to continue. Anything else will exit:  ")
                    if answer.lower() == "y":
                        continue
                    else:
                        sys.exit(1)
                elif e.err_code == GetErrorEnum.GENERIC_ERROR:
                    # This would indicate a bug in the program
                    raise
                else:
                    assert_never(e.err_code)

            except keyring.backend.errors.KeyringError as e:
                # NOTE: This should not happen simply because the password was not found.
                # This should only happen if there's some kind of bug somewhere.
                log.error(f"Could not get password from keyring: {e}")
                raise
            except Exception as e:
                log.error(
                    f"Unexpected error while trying to get password from keyring: {e}"
                )
                raise
            else:
                # Under normal circumstances, keyring.get_password() will just return
                # None instead of raising an exception. So this should be the typical
                # hot path unless there's a bug somewhere.
                if password is not None:
                    log.info("Found API key in keyring")
                    return_dict[field_name] = password
                else:
                    log.debug("No API key found in keyring")

        # NOTE: Recall how this works: any key/value pairs in this dictionary will
        # override the defaults in the Config class. It only needs to contain the
        # specific k/v pairs we want this source to override. The dictionary is
        # then passed to the next source in the chain. This makes it compatible
        # with the pydantic_settings sources chain.
        return return_dict
