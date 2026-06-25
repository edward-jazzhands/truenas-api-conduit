# third-party
from rich.traceback import install as tb_install

# project
from truenas_api_conduit.console import console_stderr, console_stdout
import truenas_api_conduit.constants as constants

__all__ = [
    "console_stderr",
    "console_stdout",
    "constants",
]

# rich tracebacks
tb_install(console=console_stderr, show_locals=False)
