import json
from typing import Any
import logging
from truenas_api_conduit import LOCK_FILE

log = logging.getLogger(__name__)


def read_lockfile() -> dict[str, Any] | None:

    try:
        with open(LOCK_FILE, "r") as f:
            lock_dict = json.loads(f.read())
        int(lock_dict["pid"])
        str(lock_dict["address"])
        int(lock_dict["socket_port"])
        return lock_dict
    except FileNotFoundError:
        log.info("Did not find a lock file")
        return
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
        log.error("Malformed lock file: %s", e)
        return
    except Exception as e:
        log.error("Unexpected error reading lock file: %s", e)
        return
