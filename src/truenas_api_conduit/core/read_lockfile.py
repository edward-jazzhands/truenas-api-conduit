import json
import logging
from dataclasses import dataclass
from truenas_api_conduit import LOCK_FILE

log = logging.getLogger(__name__)


@dataclass
class Lockfile:
    pid: int
    address: str
    header: str | None
    app_env: str


def read_lockfile() -> Lockfile | None:

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
