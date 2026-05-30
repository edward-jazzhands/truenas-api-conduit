from rich.console import Console

MAX_WIDTH: int = 120

console_stderr = Console(stderr=True)
console_stdout = Console(stderr=False)
console_stdout.__setattr__("flapjack", "flaps and jacks")

# Rich does not have a built-in setting for max width. So we allow it
# to detect the terminal size automatically, then only change it to
# the max width if its smaller than the terminal size.
console_stderr.width = min(MAX_WIDTH, console_stderr.size.width)
console_stdout.width = min(MAX_WIDTH, console_stdout.size.width)
