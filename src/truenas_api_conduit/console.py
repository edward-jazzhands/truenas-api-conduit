from rich.console import Console

MAX_WIDTH: int = 120

# NOTE: I create the stderr and stdout consoles here in order to re-use them
# across the entire program. Provides consistent formatting as well as
# saves a tiny bit of startup time. Normally Rich-Click and the logging
# RichHandler would both create their own consoles.

console_stderr = Console(stderr=True)
console_stdout = Console(stderr=False)

# Rich does not have a built-in setting for max width. So we allow it
# to detect the terminal size automatically, then only change it to
# the max width if its smaller than the terminal size.
# console_stderr.width = min(MAX_WIDTH, console_stderr.size.width)
# console_stdout.width = min(MAX_WIDTH, console_stdout.size.width)


def set_no_color() -> None:
    console_stderr.no_color = True
    console_stdout.no_color = True
