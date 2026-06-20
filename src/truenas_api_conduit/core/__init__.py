# standard library
from typing import Final
from pathlib import Path
import sys
import os
from enum import Enum, StrEnum

# third party
import platformdirs

# project
from truenas_api_conduit import APP_NAME, LOCK_FILE
from truenas_api_conduit.core.setup_app_dir import ensure_config as _ensure_config
from truenas_api_conduit.core.setup_app_dir import (
    ensure_storage_dir as _ensure_storage_dir,
)
from truenas_api_conduit.core.os_error import examine_os_error
from truenas_api_conduit.core.read_lockfile import read_lockfile, Lockfile

__all__ = [
    "ensure_config",
    "Platform",
    "Endpoints",
    "AppEnv",
    "PLATFORM",
    "CONFIG_DIR",
    "CONFIG_PATH",
    "examine_os_error",
    "read_lockfile",
    "Lockfile",
    "CRYPT_KEY_PATH",
    "CRYPT_FILE_NAME",
    "ENV",
]


class Platform(Enum):
    LINUX = "linux"
    WINDOWS = "win32"
    MACOS = "darwin"


class Endpoints(StrEnum):
    # this is a string enum because its used to build the URL like this:
    # f"http://{self.address}:{self.port}{endpoint}",

    REQUEST = "/request"
    STATUS = "/status"
    STOP = "/stop"
    RESTART = "/restart"
    LOCK = "/lock"
    UNLOCK = "/unlock"


class AppEnv(Enum):
    OS_SERVICE = "os_service"
    STANDALONE = "standalone"
    DOCKER = "docker"


ENV: Final[dict[str, str]] = {
    "truenas_address": "TRUENAS_ADDRESS",
    "api_key": "TRUENAS_API_KEY",
    "truenas_cert_path": "TRUENAS_CERT_PATH",
    "validate_certs": "TRUENAS_VALIDATE_CERTS",
    "log_level": "TRUENAS_LOG_LEVEL",
    "conduit_host": "TRUENAS_CONDUIT_HOST",
    "api_route": "TRUENAS_API_ROUTE",
    "request_header": "TRUENAS_REQUEST_HEADER",
    "stealth_mode": "TRUENAS_STEALTH_MODE",
    "crypt_key": "TRUENAS_CRYPT_KEY",
    "start_locked": "TRUENAS_START_LOCKED",
    "rich_click_theme": "RICH_CLICK_THEME",
    "no_color": "NO_COLOR",
    "editor": "EDITOR",
}


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

XDG_CONFIG_HOME: Final[Path] = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
)

CONFIG_DIR: Final[Path] = (
    XDG_CONFIG_HOME / APP_NAME if PLATFORM == Platform.LINUX else Path.home() / APP_NAME
)

CONFIG_FILE_NAME: Final[str] = "truenas-api.conf"
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


def delete_lockfile() -> str | None:
    "success: None, failure: error string"
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError as e:
        return examine_os_error(e)
    return None
