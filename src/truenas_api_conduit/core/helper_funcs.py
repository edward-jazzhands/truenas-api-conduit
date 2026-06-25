import json
import logging
from dataclasses import dataclass
from pathlib import Path
import shutil
import os
from typing import Final, TYPE_CHECKING

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

from truenas_api_conduit.app_globals import app_globals

PKG_CONFIG_FILE: Final[str] = "config.toml"


log = logging.getLogger(__name__)


@dataclass
class Lockfile:
    pid: int
    address: str
    header: str | None
    app_env: str


def create_lockfile(LOCK_FILE: Path, cfg: Config):

    if os.path.exists(LOCK_FILE):
        log.warning("Lockfile was not properly cleaned up after last run")
    log.debug("Creating lockfile")

    assert app_globals.app_env is not None, "Tried running app with no app_env set"
    cfg_dict = {
        "pid": os.getpid(),
        "address": cfg.conduit_host,  # these 2 cfg items are both in AppBaseConfig
        "header": cfg.request_header,
        "app_env": str(app_globals.app_env.value),
    }

    with open(LOCK_FILE, "w") as f:
        f.write(json.dumps(cfg_dict, indent=2))

    # windows ACLs are a pain and would require an entire third party library
    # just for this purpose. So windows users just get slightly shittier security.
    # Thats the way she goes bubs.
    LOCK_FILE.chmod(0o600)  # HACK: This won't do anything on windows.


def read_lockfile(LOCK_FILE: Path) -> Lockfile | None:

    try:
        with open(LOCK_FILE, "r") as f:
            lock_dict = json.loads(f.read())
        return Lockfile(**lock_dict)
    except FileNotFoundError:
        log.info("Did not find a lock file")
        return
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
        log.error("Malformed lock file: %s", e)
        return
    except Exception as e:
        log.error("Unexpected error reading lock file: %s", e)
        return


def delete_lockfile(LOCK_FILE: Path) -> str | None:
    "success: None, failure: error string"
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError as e:
        return examine_error(e)
    return None


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

        with as_file(files("truenas_api_conduit").joinpath(PKG_CONFIG_FILE)) as path:
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


def examine_error(e: Exception) -> str:
    """returns a string like this:
    ```
    Error type: mymodule.MyError: This is an error  (Code: 123)
      Occured while handling: SomeException: bad things happened
      Caused by: SomeOtherException: bad things happened
    ```"""

    err_string = f"Error type: {getattr(e, '__module__', 'none')}.{repr(e)} "
    err_string += str(e) if str(e) else ""

    if isinstance(e, OSError):
        if e.strerror:
            err_string += f": {e.strerror}"
        if e.errno:
            err_string += f"  (Code: {e.errno})"

    if e.__context__:
        full_context = (
            f"{getattr(e.__context__, '__module__', 'none')}.{repr(e.__context__)}"
        )
        err_string += f"\n  Occurred while handling: {full_context}"
    if e.__cause__:
        full_cause = f"{getattr(e.__cause__, '__module__', 'none')}.{repr(e.__cause__)}"
        err_string += f"\n  Caused by: {full_cause}"

    return err_string
