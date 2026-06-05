# standard library
from pathlib import Path
import shutil
import logging


def ensure_config(CONFIG_DIR: Path, CONFIG_PATH: Path) -> None:

    log = logging.getLogger(__name__)

    if not CONFIG_DIR.exists():
        log.debug("Config folder does not exist. Creating it...")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.error("Could not create the config directory. Aborting - See traceback.")
            raise
        log.debug("Config folder created successfully.")

    if not CONFIG_PATH.exists():
        from importlib.resources import files, as_file

        with as_file(files("truenas_api_conduit").joinpath("settings.toml")) as path:
            log.debug(f"Found default config file at {path}")
            log.debug(f"Copying default config file to {CONFIG_PATH}...")
            try:
                shutil.copy(path, CONFIG_PATH)
                # OR:
                # path.copy(CONFIG_PATH) # This is the newer way but its only 3.14+
            except Exception as e:
                log.error(f"Could not create the default config file: {e}")
                raise
            log.debug("Default config file copied successfully.")
    else:
        log.debug("Found existing config file.")


def ensure_storage_dir(STORAGE_DIR: Path) -> None:

    log = logging.getLogger(__name__)

    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.error("Could not create the storage directory. Aborting - See traceback.")
        raise
    log.debug("Storage directory created successfully.")
