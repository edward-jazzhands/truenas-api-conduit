# standard library
import base64
import os
import sys
import getpass
from pathlib import Path
from typing import Final
import logging
from enum import Enum

# third party
from jaraco.classes import properties
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError, PasswordSetError, KeyringError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# project
from truenas_api_conduit.errors import ConduitError
from truenas_api_conduit.core import STORAGE_DIR
from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.constants import COLORS

SECRETS_DIR: Final[Path] = STORAGE_DIR / "secrets"

__all__ = [
    "FileEncrypter",
    "PasswordGetError",
    "GetErrorEnum",
]

log = logging.getLogger(__name__)


class GetErrorEnum(Enum):
    VAULT_FILE_NOT_FOUND = 1
    SALT_FILE_NOT_FOUND = 2
    INCORRECT_ENCRYPTION_KEY = 2
    GENERIC_ERROR = 3
    NOT_A_TTY = 4


# NOTE: The keyring library purposefully does not have a PasswordGetError
# because they decided it was easier to make it return None for all errors.
# I personally don't agree with that so I've added it in. This is never going
# to be used as a third party library, so this is not an issue. If someone reading
# this were to copy this code, be aware you'll need to add in some error handling
# for this to wherever you've implemented the keyring library.
class PasswordGetError(KeyringError, ConduitError):
    """Raised when the password can't be retrieved."""

    def __init__(
        self, err_code: GetErrorEnum, causing_exception: Exception | None = None
    ):
        self.causing_exception = causing_exception
        self.err_code = err_code
        super().__init__(err_code)


class NotATTYError(KeyringError, ConduitError):
    pass


class FileEncrypter(KeyringBackend):

    def __init__(self):
        self.help_message_shown: bool = False
        self.help_message = (
            "Using the file encrypter keyring backend. This is selected when "
            "there is no other keyring backend available. You will be prompted "
            "to enter an encryption key to store and retrieve your API key.\n"
            f"You can also set the [{COLORS.envvar}]TRUENAS_CRYPT_KEY[default] environment "
            "variable to avoid this prompt."
        )
        super().__init__()

    @properties.classproperty
    def priority(cls):
        return 0.0
        # NOTE: The priority is set to 0.0 so that its absolute
        # lowest priority in case nothing else is found.

    def set_password(self, service: str, username: str, password: str):

        SECRETS_DIR.mkdir(parents=True, exist_ok=True)

        # NOTE: The 'one key per file' system:
        # this works basically the same as storing all the passwords in a single
        # file. But with this method, we don't need to worry about file format
        # or json decoding or any of that stuff. The file name itself is the
        # service and username, and the password is the file contents.

        # We intentionally do not check whether the file exists already. The
        # expected behavior of a keyring backend is to silently overwrite any
        # existing passwords.
        vault_file: Path = self._get_vault_file(service, username)
        salt_file = vault_file.with_suffix(".salt")
        self._ensure_salt_file(salt_file)

        # Derive the encryption key from the user's password + salt, then hand it
        # to Fernet which handles the actual AES encryption and HMAC authentication.
        try:
            f = Fernet(self._derive_key(salt_file.read_bytes(), service, username))
            # Each call to Fernet.encrypt() generates a new random initialization vector
            vault_file.write_bytes(f.encrypt(password.encode()))
            vault_file.chmod(0o600)
        except Exception as e:
            log.error("Could not set password in keyring: %s", e)
            raise PasswordSetError(f"Could not set password in keyring: {e}") from e

    def get_password(self, service: str, username: str) -> str | None:

        vault_file: Path = self._get_vault_file(service, username)
        salt_file = vault_file.with_suffix(".salt")

        if not vault_file.exists():
            raise PasswordGetError(err_code=GetErrorEnum.VAULT_FILE_NOT_FOUND)
        else:
            if not salt_file.exists():
                raise PasswordGetError(err_code=GetErrorEnum.SALT_FILE_NOT_FOUND)

        # Use password + salt file to reproduce the Fernet
        try:
            f = Fernet(self._derive_key(salt_file.read_bytes(), service, username))
            return f.decrypt(vault_file.read_bytes()).decode()
        except InvalidToken as e:
            raise PasswordGetError(err_code=GetErrorEnum.INCORRECT_ENCRYPTION_KEY) from e
        except NotATTYError as e:
            raise PasswordGetError(err_code=GetErrorEnum.NOT_A_TTY) from e
        except Exception as e:
            log.error("Could not get password from keyring: %s", e)
            raise PasswordGetError(err_code=GetErrorEnum.GENERIC_ERROR) from e

    def delete_password(self, service: str, username: str):

        vault_file: Path = self._get_vault_file(service, username)
        salt_file = vault_file.with_suffix(".salt")

        # The one key/one file system makes things easy here, we just delete the files
        if vault_file.exists():
            try:
                vault_file.unlink(missing_ok=True)
                salt_file.unlink(missing_ok=True)
            except Exception as e:
                log.error("Could not delete password from keyring: %s", e)
                raise PasswordDeleteError(
                    f"Could not delete password from keyring: {e}"
                ) from e
        else:
            raise PasswordDeleteError("Nothing to delete.")

    def _derive_key(self, salt: bytes, service: str, username: str) -> bytes:

        # PBKDF2 runs the password through SHA256 thousands of times (480,000 iterations here).
        # The point of this is to make brute-forcing expensive -- each guess requires
        # 480,000 hash operations rather than one. The salt is random bytes mixed in
        # so that the same password doesn't produce the same key across different installs,
        # which defeats precomputed lookup tables.
        # The output is 32 raw bytes, which we base64-encode because Fernet expects
        # a url-safe base64 key rather than raw bytes.
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000
        )

        crypt_key = os.environ.get("TRUENAS_CRYPT_KEY")
        if crypt_key is None:
            if sys.stdin.isatty():
                if not self.help_message_shown:
                    console_stderr.print(self.help_message)
                    self.help_message_shown = True
                console_stderr.print(
                    "Enter an encryption key for secret: "
                    f"[{COLORS.envvar}]{service}.{username}[default]\n"
                )
                crypt_key = getpass.getpass(stream=sys.stderr)

                # NOTE: This implementation will ask for the encryption key for every
                # secret that is retrieved from it. This is not ideal, but it allows
                # me to avoid storing the key in memory. In the case of this program,
                # there's just the one secret, so it's not a big deal. But if I use
                # this to store multiple secrets for something in the future, this
                # could get annoying.
            else:
                raise NotATTYError("No env var set and stdin is not a TTY")
        else:
            log.info("Using TRUENAS_CRYPT_KEY environment variable")

        safebytes = base64.urlsafe_b64encode(kdf.derive(crypt_key.encode()))
        del crypt_key  #  so its not stored in memory
        return safebytes

    @staticmethod
    def _ensure_salt_file(salt_file: Path) -> None:

        # The salt is generated once for every new secret added to the keyring.
        # It does not need to be secret, its only job is to be unique so that the same
        # password produces a different derived key on each machine.
        if not salt_file.exists():
            tmp = salt_file.with_suffix(".tmp")
            tmp.write_bytes(os.urandom(16))
            tmp.replace(salt_file)
            salt_file.chmod(0o600)

    @staticmethod
    def _get_vault_file(service: str, username: str) -> Path:
        """encode the service and username so it can't contain slashes or
        break the filesystem"""

        # this looks wierd AF. What its doing is encoding the original text into bytes
        # so the Base64 library can process it, and then decoding the resulting Base64
        # bytes back into text so you can use it as a standard file name. This is often
        # referred to as the "byte sandwich" pattern
        safe_name = base64.urlsafe_b64encode(f"{service}::{username}".encode()).decode()
        return SECRETS_DIR / safe_name
