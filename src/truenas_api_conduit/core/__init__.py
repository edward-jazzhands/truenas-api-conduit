# standard library
from typing import Final
from pathlib import Path
import sys

# third party
import platformdirs

# project
from truenas_api_conduit import APP_NAME, Platform
from truenas_api_conduit.core.setup_app_dir import ensure_config as _ensure_config
from truenas_api_conduit.core.setup_app_dir import (
    ensure_storage_dir as _ensure_storage_dir,
)
from truenas_api_conduit.core.os_error import examine_os_error
from truenas_api_conduit.core.msg_receiver import MessageReceiver

__all__ = [
    "ensure_config",
    "Platform",
    "PLATFORM",
    "CONFIG_DIR",
    "CONFIG_PATH",
    "examine_os_error",
    "MessageReceiver",
    "CRYPT_KEY_PATH",
    "CRYPT_FILE_NAME",
]


def detect() -> Platform:
    match sys.platform:
        case "linux":
            return Platform.LINUX
        case "win32":
            return Platform.WINDOWS
        case "darwin":
            return Platform.MACOS
        case _:
            raise RuntimeError(f"Unknown Operating System: {sys.platform}")


PLATFORM: Final[Platform] = detect()

SLASH: Final[str] = "/" if PLATFORM == Platform.LINUX else "\\"

# NOTE: It does not make sense to use platformdirs here because the config file
# must be edited manually by the user. On Windows and MacOS, the conventional
# app data directories are hidden from users by default, so average users
# wouldn't be able to find the config file (these locations are intended for
# programs that manage their own data internally).
# Since we need the user to edit the config file, for Windows and MacOS we
# place the config folder directly in the home directory. This is considered
# standard practice for cross-platform apps with a user-editable config file.
# For Linux we follow the XDG Base Directory specification instead.

CONFIG_DIR: Final[Path] = (
    Path.home() / ".config" / APP_NAME
    if PLATFORM == Platform.LINUX
    else Path.home() / APP_NAME
)

CONFIG_FILE_NAME: Final[str] = "settings.conf"
CONFIG_PATH: Final[Path] = CONFIG_DIR / CONFIG_FILE_NAME


def ensure_config() -> None:
    _ensure_config(CONFIG_DIR, CONFIG_PATH)


# Now we actually do need to use platformdirs for the internal storage dir
# On Windows this will be: C:\Users\<username>\AppData\Roaming\truenas-api-conduit
# On MacOS: ~/Library/Application Support/truenas-api-conduit
# On Linux: ~/.local/share/truenas-api-conduit
STORAGE_DIR: Final[Path] = Path(platformdirs.user_data_dir(APP_NAME))

CRYPT_FILE_NAME: Final[str] = ".cryptkey"
CRYPT_KEY_PATH: Final[Path] = CONFIG_DIR / CRYPT_FILE_NAME


def ensure_storage_dir() -> None:
    _ensure_storage_dir(STORAGE_DIR)
