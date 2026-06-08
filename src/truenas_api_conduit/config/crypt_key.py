# standard library
from typing import assert_never
import os
import logging

# third party
from pydantic import SecretStr
from truenas_api_conduit.console import console_stderr
import click

# project
from truenas_api_conduit.core import CONFIG_DIR, CRYPT_KEY_FILE, SLASH
from truenas_api_conduit.constants import COLORS

log = logging.getLogger(__name__)

# NOTE: the crypt_key_callback is a function which  provides a way for the
# FileEncrypter keyring backend to get the crypt_key value from a user prompt
# and then use the callback to set it on the Config class.

# This hack is necessary because the FileEncrypter keyring backend is only
# used as a fallback when no other keyring backends are available, and its
# the only backend that requires the user to enter an encryption key.
# We don't know if it's going to be used or not, until the keyring library
# has decided to use it. So the FileEncrypter needs a way to forward the
# password back to the pydantic model, in the event that it's used.

# The reason for this is to prevent the user from needing to enter
# the encryption key more than once when the program starts up. The CLI
# passes the config to the service as JSON, which previously required
# the user to enter the encryption key twice because there was no way
# to store the encryption key in the config model.

# The stored crypt_key (as a pydantic SecretStr) is converted into the
# crypt_key field in the model_post_init method, which reads from the
# crypt_keys dict.


def store_crypt_key(v: SecretStr) -> bool | None:

    if CRYPT_KEY_FILE.exists():
        return

    console_stderr.print(
        "Do you want to store this encryption key in a file?\n"
        "To start the service automatically (ie. at login), you'll need to "
        "get this encryption key into the program. You can do this by:\n"
        f"  1. Setting the environment variable "
        f"\\[env: [{COLORS.envvar}]TRUENAS_CRYPT_KEY[default]=] "
        "(ensure it is set before the service starts)\n"
        f"  2. Creating a file named [{COLORS.envvar}].crypt[default] "
        "in your config directory containing the encryption key "
        f"(set [{COLORS.command}]chmod 600[default])\n\n"
        f"[default]On your machine, it would look for this file at: "
        f"{CONFIG_DIR}{SLASH}.crypt\n"
        "Choosing yes will perform option 2 for you."
    )
    answer = click.prompt("Enter 'y' to create the .crypt file")
    if answer.lower() not in ("y", "yes"):
        return

    if isinstance(v, SecretStr):
        CRYPT_KEY_FILE.write_text(v.get_secret_value())
    elif isinstance(v, str):
        CRYPT_KEY_FILE.write_text(v)
    else:
        assert_never(v)

    CRYPT_KEY_FILE.chmod(0o600)  # HACK: This won't do anything on windows.
    console_stderr.print("Success: encryption key written to file")
    return True


def get_crypt_key() -> SecretStr | None:

    crypt_key = os.environ.get("TRUENAS_CRYPT_KEY")
    if crypt_key is not None:
        crypt_key = SecretStr(crypt_key)
        log.debug("Found TRUENAS_CRYPT_KEY in env: %s", crypt_key)
    else:
        if CRYPT_KEY_FILE.exists():
            try:
                crypt_key = SecretStr(CRYPT_KEY_FILE.read_text())
            except Exception as e:
                log.critical("Could not read crypt key file: %s", e)
                raise
            else:
                log.debug("Found crypt key in file: %s", crypt_key)
    return crypt_key
