from typing import Final
from importlib.metadata import version
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.traceback import install as tb_install

from truenas_api_conduit.log_setup import logging_manager
from truenas_api_conduit.console import console_stderr

__all__ = [
    "APP_NAME",
    "SERVICENAME",
    "LOCK_FILE",
    "__version__",
    "logging_manager",
    "COLORS",
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


@dataclass(frozen=True)
class COLORS:
    command: str = "deep_sky_blue1"
    envvar: str = "orange1"
    option: str = "bold cyan"
