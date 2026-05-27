# standard library
from typing import Final
from pathlib import Path

from truenas_api_conduit import APP_NAME
from .detect_platform import Platform
from . import log_setup
from .setup_app_dir import ensure_config as _ensure_config

__all__ = [
    "log_setup",
    "ensure_config",
    "Platform",
    "PLATFORM",
    "CONFIG_DIR",
    "CONFIG_PATH",
]

PLATFORM: Final = detect_platform.detect()

CONFIG_DIR: Final = (
    Path.home() / ".config" / APP_NAME
    if PLATFORM == Platform.LINUX
    else Path.home() / APP_NAME
)

CONFIG_PATH: Final = CONFIG_DIR / "settings.conf"

# NOTE: It does not make sense to use platformdirs here because the config file
# must be edited manually by the user. On Windows and MacOS, the conventional
# app data directories are hidden from users by default, so average users
# wouldn't be able to find the config file (these locations are intended for
# programs that manage their own data internally).
# Since we need the user to edit the config file, for Windows and MacOS we
# place the config folder directly in the home directory. This is considered
# standard practice for cross-platform apps with a user-editable config file.
# For Linux we follow the XDG Base Directory specification instead.


def ensure_config() -> None:
    _ensure_config(CONFIG_DIR, CONFIG_PATH)