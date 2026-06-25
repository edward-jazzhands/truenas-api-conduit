from truenas_api_conduit.core.helper_funcs import (
    ensure_config,
    ensure_storage_dir,
    examine_error,
    read_lockfile,
    Lockfile,
    delete_lockfile,
    create_lockfile,
)
from truenas_api_conduit.constants import CONFIG_DIR, CONFIG_PATH, STORAGE_DIR

__all__ = [
    "ensure_app_dirs",
    "examine_error",
    "read_lockfile",
    "delete_lockfile",
    "create_lockfile",
    "Lockfile",
]


def ensure_app_dirs() -> None:
    ensure_config(CONFIG_DIR, CONFIG_PATH)
    ensure_storage_dir(STORAGE_DIR)
