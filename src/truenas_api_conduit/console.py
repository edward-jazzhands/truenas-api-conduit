from rich.console import Console

# NOTE: I create the stderr and stdout consoles here in order to re-use them
# across the entire program. Provides consistent formatting as well as
# saves a tiny bit of startup time. Normally Rich-Click and the logging
# RichHandler would both create their own consoles.

console_stderr = Console(stderr=True)
console_stdout = Console(stderr=False)


def set_no_color() -> None:
    console_stderr.no_color = True
    console_stdout.no_color = True

# ! not used anymore
def set_max_width(width: int) -> None:
    console_stderr.width = min(width, console_stderr.size.width)
    console_stdout.width = min(width, console_stdout.size.width)