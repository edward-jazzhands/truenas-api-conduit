from rich.traceback import install as tb_install

from truenas_api_conduit.constants import APP_NAME, LOCK_FILE, __version__
import truenas_api_conduit.log_setup as log_setup  # <- this is run on import
from truenas_api_conduit.console import console_stderr

__all__ = [
    "APP_NAME",
    "LOCK_FILE",
    "__version__",
    "log_setup",
]

# rich tracebacks
tb_install(console=console_stderr, show_locals=False)
