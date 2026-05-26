# standard library
from typing import Final
import sys
from pathlib import Path
import shutil
import logging

from truenas_api_conduit import APP_NAME

log = logging.getLogger(__name__)

__all__ = ["CONFIG_PATH", "CONFIG_DIR", "ensure_config"]


# NOTE: It does not make sense to use platformdirs here because the config file
# must be edited manually by the user. On Windows and MacOS, the conventional
# app data directories are hidden from users by default, so average users
# wouldn't be able to find the config file (these locations are intended for
# programs that manage their own data internally).
# Since we need the user to edit the config file, for Windows and MacOS we
# place the config folder directly in the home directory. This is considered
# standard practice for cross-platform apps with a user-editable config file.
# For Linux we follow the XDG Base Directory specification instead.
if sys.platform == "linux":
    CONFIG_DIR: Final = Path.home() / ".config" / APP_NAME
    log.debug("Detected Linux")
else:
    CONFIG_DIR: Final = Path.home() / APP_NAME
    if sys.platform == "win32":
        log.debug("Detected Windows")
    elif sys.platform == "darwin":
        log.debug("Detected MacOS")
    else:
        log.debug("Unknown Operating System")

CONFIG_PATH: Final = CONFIG_DIR / "settings.conf"


def ensure_config() -> None:

    if not CONFIG_DIR.exists():
        log.debug("Config folder does not exist. Creating it...")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.error("Could not create the config directory. Aborting - See traceback.")
            raise
        log.debug("Config folder created successfully.")

    if not CONFIG_PATH.exists():
        settings_file_path = Path(__file__).parent / "settings.toml"
        log.debug(f"Copying default config file to {CONFIG_PATH}...")
        try:
            shutil.copy(settings_file_path, CONFIG_PATH)
            # OR:
            # settings_file.copy(CONFIG_PATH) # This is the newer way but its only 3.14+
        except Exception as e:
            log.error(f"Could not create the default config file: {e}")
            raise
        log.debug("Default config file copied successfully.")
    else:
        log.debug("Found existing config file.")
