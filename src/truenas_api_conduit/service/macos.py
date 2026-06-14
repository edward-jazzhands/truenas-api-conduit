"""
MacOS service implementation using launchd.
"""

# standard library
import shutil
import subprocess
import sys
import os
import plistlib
from pathlib import Path
import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

# local
from truenas_api_conduit import APP_NAME, SERVICENAME
import truenas_api_conduit.core as core
from truenas_api_conduit.service.base import BaseService, ServiceError
from truenas_api_conduit.console import console_stdout  # , console_stderr

# NOTE: log messages are configured to go to stderr
log = logging.getLogger(__name__)

# UNIT_NAME: Final[str] = f"{APP_NAME}.service"
# SYSTEMD_USER_DIR: Final[Path] = (
#     Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
#     / "systemd"
#     / "user"
# )
# UNIT_FILE = SYSTEMD_USER_DIR / UNIT_NAME

__all__ = [
    "MacOSService",
]


def build_plist(executable: Path) -> bytes:
    label: str = f"io.{APP_NAME}"

    # NOTE: macOS doesn't have a true equivalent to journald. launchd's logging 
    # goes through asl/unified logging if you don't set StandardOutPath/StandardErrorPath,
    # but capturing that programmatically is more complex than journal queries.
    # Redirecting to log files is the normal approach in apple ecosystem.

    # Wants=network-online.target / After=network-online.target has no direct launchd
    # equivalent. launchd doesn't have ordering/dependency targets like systemd. If
    # network availability matters at startup, on MacOS that is typically handled by
    # your program (retry logic) rather than at the service-manager level. Luckily this
    # program already has retry logic built-in so we gucci.

    plist_dict: dict[str, object] = {
        "Label": label,
        "ProgramArguments": [str(executable)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": f"/usr/local/var/log/{APP_NAME}.log",
        "StandardErrorPath": f"/usr/local/var/log/{APP_NAME}.err.log",
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "TRUENAS_APP_ENV": "os_service",
        },
    }

    return plistlib.dumps(plist_dict)

def write_plist(executable: Path, dest: Path) -> None:
    dest.write_bytes(build_plist(executable))

class MacOSService(BaseService):

    def install(self) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self) -> None:
        pass

    def status(self, forward_stdout: bool = True) -> int:
        return 0

    def detect_service(self) -> core.AppEnv:
        if True:
            return core.AppEnv.OS_SERVICE
        else:
            return core.AppEnv.STANDALONE


    def logs(self, follow: bool = False, limit: int = 100) -> str | None:

        pass