from typing import Final
from importlib.metadata import version
import tempfile
from dataclasses import dataclass
from pathlib import Path
from enum import Enum, StrEnum

from rich.traceback import install as tb_install

import truenas_api_conduit.log_setup as log_setup  # <- this is run on import
from truenas_api_conduit.console import console_stderr

__all__ = [
    "APP_NAME",
    "SERVICENAME",
    "LOCK_FILE",
    "__version__",
    "log_setup",
    "COLORS",
    "Platform",
    "InstallType",
    "Endpoints",
]

# rich tracebacks
tb_install(console=console_stderr, show_locals=False)


APP_NAME: Final[str] = "truenas-api-conduit"
SERVICENAME: Final[str] = f"{APP_NAME}d"

# Linux/Mac -> /tmp/my_app.lock
# Windows -> C:\Users\<user>\AppData\Local\Temp\my_app.lock
LOCK_FILE: Final[Path] = Path(tempfile.gettempdir()) / f"{APP_NAME}.lock"

# tempfile.gettempdir() checks TMPDIR, TEMP, and TMP env vars before falling back to
# platform defaults so it respects user/system overrides.

__version__: Final[str] = version(APP_NAME)


class Platform(Enum):
    LINUX = "linux"
    WINDOWS = "win32"
    MACOS = "darwin"


class InstallType(Enum):
    USER = "user"
    SYSTEM = "system"
    PACKAGE = "package"


class Endpoints(StrEnum):
    # this is a string enum because its used to build the URL like this:
    # f"http://{self.address}:{self.port}{endpoint}",

    REQUEST = "/request"
    STATUS = "/status"
    STOP = "/stop"
    RESTART = "/restart"


@dataclass(frozen=True)
class COLORS:
    command: str = "deep_sky_blue1"
    envvar: str = "orange1"
    option: str = "bold cyan"
