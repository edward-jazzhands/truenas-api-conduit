# standard library
import base64
import os
import sys
import getpass
from pathlib import Path
from typing import Final, assert_never
import logging
from enum import Enum

# third party
import click
from jaraco.classes import properties
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError, PasswordSetError, KeyringError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from pydantic import SecretStr

# project
from truenas_api_conduit.errors import ConduitError
from truenas_api_conduit.constants import (
    STORAGE_DIR,
    CONFIG_DIR,
    CRYPT_KEY_PATH,
    CRYPT_FILE_NAME,
    SLASH,
    ENV,
    COLORS,
)
from truenas_api_conduit.console import console_stderr

SECRETS_DIR: Final[Path] = STORAGE_DIR / "secrets"
SALT_LENGTH: Final[int] = 16

__all__ = [
    "FileEncrypter",
    "PasswordGetError",
    "GetErrorEnum",
]

log = logging.getLogger(__name__)


class GetErrorEnum(Enum):
    VAULT_FILE_NOT_FOUND = 1
    INCORRECT_ENCRYPTION_KEY = 2
    GENERIC_ERROR = 3
    NOT_A_TTY = 4


# NOTE: The keyring library purposefully does not have a PasswordGetError
# because they decided it was easier to make it return None for all errors.
# I personally don't agree with that so I've added it in. This is not going
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

    def __repr__(self) -> str:
        return f"PasswordGetError(err_code={self.err_code}, causing_exception={self.causing_exception})"

    def __str__(self) -> str:
        return self.err_code.name


class NotATTYError(KeyringError, ConduitError):
    pass


class FileEncrypter(KeyringBackend):

    def __init__(self, crypt_key: SecretStr | None = None):
        "Ed's custom file encrypter keyring backend"

        self.crypt_key = crypt_key
        self.help_message_shown: bool = False
        self.help_message = (
            "Using the file encrypter keyring backend. This is selected when "
            "there is no other keyring backend available. You will be prompted "
            "to enter an encryption key to store and retrieve your API key.\n"
            "There's several ways you can avoid this warning:\n"
            f"- Set the [{COLORS.envvar}]{ENV['crypt_key']}[default] environment variable\n"
            f"- Create a [{COLORS.envvar}]{CRYPT_FILE_NAME}[default] file in the config "
            f"dir. (The [{COLORS.command}]set-key[default] command in the CLI will "
            f"offer to create a {CRYPT_FILE_NAME} file for you)\n"
            f"- Start the service in locked mode (env var, config file, --locked option)"
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

        # The salt is generated once for every new secret added to the keyring.
        # It does not need to be secret, its only job is to be unique so that the same
        # password produces a different derived key on each machine.
        salt = os.urandom(SALT_LENGTH)

        # Derive the encryption key from the user's password + salt, then hand it
        # to Fernet which handles the actual AES encryption and HMAC authentication.
        try:
            f = Fernet(self._derive_key(salt, service, username))
            # Each call to Fernet.encrypt() generates a new random initialization vector
            vault_file.write_bytes(salt + f.encrypt(password.encode()))
            vault_file.chmod(0o600)  # HACK: This won't do anything on windows.
        except Exception as e:
            log.error("Could not set password in keyring: %s", e)
            raise PasswordSetError(f"Could not set password in keyring: {e}") from e
        else:
            log.debug("Success: key set")
            self.store_crypt_key()
            self.crypt_key = None

    def get_password(self, service: str, username: str) -> str | None:

        # if there was a crypt key set through init args (CLI), and its wrong,
        # fail immediately. Otherwise 2 retries
        ALLOWED: Final[int] = 3 if not self.crypt_key else 1
        attempts = 0
        while True:
            try:
                if attempts > 0:
                    console_stderr.print(f"Attempts remaining: {ALLOWED - attempts}")
                return self._get_password(service, username)
            except PasswordGetError as e:
                if e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
                    attempts += 1
                    if attempts < ALLOWED:
                        continue
                return self._handle_password_get_error(e, service, username)

    def _get_password(self, service: str, username: str) -> str | None:

        vault_file: Path = self._get_vault_file(service, username)

        if not vault_file.exists():
            raise PasswordGetError(err_code=GetErrorEnum.VAULT_FILE_NOT_FOUND)

        vbytes = vault_file.read_bytes()
        salt, ciphertext = vbytes[:SALT_LENGTH], vbytes[SALT_LENGTH:]

        # Use password + salt file to reproduce the Fernet
        try:
            f = Fernet(self._derive_key(salt, service, username))
            return f.decrypt(ciphertext).decode()
        except (InvalidToken, EOFError) as e:
            raise PasswordGetError(err_code=GetErrorEnum.INCORRECT_ENCRYPTION_KEY) from e
        except NotATTYError as e:
            raise PasswordGetError(err_code=GetErrorEnum.NOT_A_TTY) from e
        except Exception as e:
            log.error("Could not get password from keyring: %s", e)
            raise PasswordGetError(err_code=GetErrorEnum.GENERIC_ERROR) from e

    def delete_password(self, service: str, username: str):

        vault_file: Path = self._get_vault_file(service, username)

        # The one key/one file system makes things easy here, we just delete the files
        if vault_file.exists():
            try:
                vault_file.unlink(missing_ok=True)
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

        # NOTE: self.crypt_key would be set if the crypt key was passed in as a
        # constructor argument to the Config class. This isn't a CLI option,
        # it's used by the unlock command.
        if self.crypt_key:
            crypt_key = self.crypt_key.get_secret_value()
            self.crypt_key = None  # remove after using
        else:
            crypt_key = self._get_crypt_key(service, username)

        safebytes = base64.urlsafe_b64encode(kdf.derive(crypt_key.encode()))
        return safebytes

    def _get_crypt_key(self, service: str, username: str) -> str:

        crypt_key = self.get_crypt_key()
        if crypt_key is not None:
            log.warning("Found a stored encryption key, using it.")
            return crypt_key.get_secret_value()

        if sys.stdin.isatty():
            if not self.help_message_shown:
                log.warning(self.help_message)
                self.help_message_shown = True
            console_stderr.print(
                "\nEnter encryption key for secret: "
                f"[{COLORS.envvar}]{service}.{username}[default]"
            )
            return getpass.getpass(stream=sys.stderr)
            # crypt_key = getpass.getpass(stream=sys.stderr)
            # self.crypt_key = SecretStr(crypt_key)
            # return self.crypt_key.get_secret_value()
        else:
            raise NotATTYError("No env var set and stdin is not a TTY")

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

    @staticmethod
    def _handle_password_get_error(
        e: PasswordGetError, service: str, username: str
    ) -> None:

        # This is my custom error class so it will only happen if keyring tried
        # to use my fallback FileEncrypter backend, and the user password was
        # not found.
        if e.err_code == GetErrorEnum.NOT_A_TTY:
            log.warning(
                "There's an encrypted API key stored, but could not find the "
                "encryption key, and stdin is not a TTY, so the user cannot be "
                "prompted for it."
            )
            return
        elif e.err_code == GetErrorEnum.VAULT_FILE_NOT_FOUND:
            log.debug("No vault file found for: %s.%s", service, username)
            return
        elif e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
            raise e
        elif e.err_code == GetErrorEnum.GENERIC_ERROR:
            # This would indicate a bug in the program
            raise e
        else:
            assert_never(e.err_code)

    def store_crypt_key(self) -> None:

        if not self.crypt_key:
            raise RuntimeError("Tried to store a crypt key but no key was set")

        console_stderr.print(
            "Do you want to store this encryption key in a file?\n"
            "To start the service automatically (ie. at login), you'll need to "
            "get this encryption key into the program. You can do this by:\n"
            f"  1. Setting the environment variable "
            f"\\[env: [{COLORS.envvar}]{ENV['crypt_key']}[default]=] "
            "(ensure it is set before the service starts)\n"
            f"  2. Creating a file named [{COLORS.envvar}]{CRYPT_FILE_NAME}[default] "
            "in your config directory containing the encryption key "
            f"(set [{COLORS.command}]chmod 600[default])\n\n"
            f"[default]On your machine, it would look for this file at: "
            f"{CONFIG_DIR}{SLASH}{CRYPT_FILE_NAME}\n"
            "Choosing yes will perform option 2 for you."
        )
        answer = click.prompt(f"Enter 'y' to create the {CRYPT_FILE_NAME} file")
        if answer.lower() not in ("y", "yes"):
            return

        CRYPT_KEY_PATH.write_text(self.crypt_key.get_secret_value())
        CRYPT_KEY_PATH.chmod(0o600)  # HACK: This won't do anything on windows.
        console_stderr.print("Success: encryption key written to file")

    @staticmethod
    def get_crypt_key() -> SecretStr | None:

        crypt_key = os.environ.get(ENV["crypt_key"])
        if crypt_key is not None:
            crypt_key = SecretStr(crypt_key)
            log.debug("Found %s in environment: %s", ENV["crypt_key"], crypt_key)
        else:
            if CRYPT_KEY_PATH.exists():
                try:
                    crypt_key = SecretStr(CRYPT_KEY_PATH.read_text())
                except Exception as e:
                    log.critical("Could not read crypt key file: %s", e)
                    raise
                else:
                    log.debug("Found crypt key in file: %s", crypt_key)
        return crypt_key
