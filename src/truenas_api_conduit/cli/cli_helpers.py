# standard library
import sys
import os
import shutil
import enum
import logging
from dataclasses import dataclass
from typing import Any

# third-party
import rich_click as click
from rich.panel import Panel

# from rich.text import Text
# from rich.highlighter import Highlighter

# project
import truenas_api_conduit.core as core
from truenas_api_conduit.i18n import _, yes_key, no_key
from truenas_api_conduit.app_globals import app_globals
from truenas_api_conduit.constants import COLORS
from truenas_api_conduit.console import console_stderr, set_no_color

# * l18n status: DONE


__all__ = [
    "CLIOptions",
    "make_usage_error_panel",
    "make_success_panel",
    "require_tty",
    "cli_print",
    "cli_print_setup",
    "prompt_for_config",
]

@dataclass
class CLIOptions:

    verbose: int = 0
    truenas_address: str | None = None
    conduit_host: str | None = None
    api_key: bool | None = None
    crypt_key: str | None = None
    start_locked: bool | None = None
    validate_certs: bool | None = None
    log_level: str | None = None
    no_color: bool | None = None
    pretty: bool | None = None


def make_usage_error_panel(err_string: str, title: str = "Error") -> Panel:
    err_string = "[default]" + err_string
    return Panel(err_string, title=title, title_align="left", style="bright_red")


def make_success_panel(msg: str, title: str = "Success") -> Panel:
    msg = "[default]" + msg
    return Panel(msg, title=title, title_align="left", style="bright_green")


def require_tty(needed_for: str, additional: str = "") -> None:
    if not sys.stdin.isatty():
        console_stderr.print(
            _("Cannot prompt for {needed_for}: stdin is not a TTY.").format(needed_for=needed_for)
        )
        if additional:
            console_stderr.print(additional)
        sys.exit(1)


levels_translated = {
    "debug": _("DEBUG"),
    "info": _("INFO"),
    "warning": _("WARNING"),
    "error": _("ERROR"),
}


class Verbosity(enum.IntEnum):
    debug = 3
    info = 2
    warning = 1
    error = 0

verbosity_mapping = {
    "DEBUG": Verbosity.debug,
    "INFO": Verbosity.info,
    "WARNING": Verbosity.warning,
    "ERROR": Verbosity.error,
}

verbosity_mapping_reverse = {
     Verbosity.debug: "DEBUG",
     Verbosity.info: "INFO",
     Verbosity.warning: "WARNING",
     Verbosity.error: "ERROR",
}

class CLIPrinter:
    "controls output based on verbsoity level. prints to stderr"

    def __init__(self, verbosity: int = 1):  # warning by default
        self.verbosity = verbosity

    def __str__(self) -> str:
        return _("CLIPrinter(verbosity={verbosity})").format(verbosity=self.verbosity)

    def debug(self, *values: Any, show_level: bool = True) -> None:
        self._print(Verbosity.debug, *values, show_level=show_level)

    def info(self, *values: Any, show_level: bool = True) -> None:
        self._print(Verbosity.info, *values, show_level=show_level)

    def warning(self, *values: Any, show_level: bool = True) -> None:
        self._print(Verbosity.warning, *values, show_level=show_level)

    def error(self, *values: Any, show_level: bool = True) -> None:
        self._print(Verbosity.error, *values, show_level=show_level)


    def print_record(self, record: logging.LogRecord, show_level: bool = True) -> None:

        verbosity = verbosity_mapping[record.levelname]
        if verbosity > self.verbosity:
            return

        raw_message = record.getMessage()
        
        translated = levels_translated[verbosity.name]
        level_color = COLORS[verbosity.name] 

        if cli_print.verbosity >= Verbosity.debug:
            # TODO: Localization
            # timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
            # meta_prefix = f"{record.levelname}"
            
            location = f"[gray50]{record.module}[/]:{record.lineno}"
            location_width = len(f"{record.module}:{record.lineno}")

            with_lvl = f"[{level_color}] {translated:<9} [/]{raw_message}"
            msg_width = len(raw_message) + 11
            
            term_width = shutil.get_terminal_size(fallback=(1, 1)).columns
            filler = term_width - msg_width - location_width
            
            console_stderr.print(f"{with_lvl}{' ' * filler}{location}")
        else:
            console_stderr.print(raw_message)


    def _print(self, verbosity: Verbosity, *values: str, show_level: bool) -> None:
        """Used to print the CLIOptions object to stdout"""
        
        # if incoming verbosity is higher than current setting, do nothing
        # ex. self.verbosity = 1(info), verbosity = 2(debug), do nothing
        if verbosity > self.verbosity:
            return

        translated = levels_translated[verbosity.name]
        level_color = COLORS[verbosity.name] 
        if show_level:
            console_stderr.print(f"[{level_color}] {translated:<9}[/]", *values)
        else:
            console_stderr.print(*values)

# * For export:
cli_print = CLIPrinter()


def cli_print_setup(cli_options: CLIOptions) -> CLIPrinter:

    if cli_options.verbose >= 4:
        cli_options.verbose = 3

    nc_env = os.environ.get("NO_COLOR")
    if nc_env is not None or cli_options.no_color:
        set_no_color()

    # 0 = error, 1 = warning, 2 = info, 3 = debug, 4 = trace
    # we add 1 because we want the starting level to be warning.
    # so for the user this feels like v = info, vv = debug, vvv = trace
    cli_print.verbosity = cli_options.verbose + 1

    if cli_options.verbose >= 3:
        app_globals.set_cli_trace(True)
        cli_print.debug("CLI tracebacks enabled")

    if cli_options.verbose > 1:
        console_stderr.print()

    from truenas_api_conduit.cli import logging_manager

    cli_print.debug(f"{logging_manager.app_env = }")

    verbosity = Verbosity(cli_options.verbose)
    lvl_name = verbosity_mapping_reverse[verbosity]
    lvl_int = logging.getLevelNamesMapping()[lvl_name]
    
    # NOTE: If verbose is not set (0), the default level will be WARNING, and
    # this will set a null handler for all logging.
    # If verbose is used (set to 1 or higher), this will assign the CLI handler
    # which will redirect all logs to the CLI printer.
    logging_manager.set_log_level(lvl_int)
    return cli_print


def prompt_for_config() -> None:
    """Used in commands that need to ensure the config dir exists, but without
    triggering the full pydantic config validation: set-key, and config. In case
    the user tries to open/read the config file before they've run the program
    for the first time."""
    
    if not core.CONFIG_DIR.exists():

        if sys.stdin.isatty():

            prompt_msg = _(
                "The config directory has not been created yet. "
                "Do you want to create it now?"
            )

            answer = click.prompt(
                prompt_msg,
                type=click.Choice([yes_key, no_key], case_sensitive=False),
                default=yes_key
            )
            if answer.lower() != yes_key.lower():
                console_stderr.print("Cancelled")
                sys.exit(1)

        core.ensure_app_dirs()

    if not core.CONFIG_PATH.exists():
        if sys.stdin.isatty():

            prompt_msg = _(
                "The config file is missing. Do you want to create a new one "
                "with the default settings?"
            )

            answer = click.prompt(
                prompt_msg,
                type=click.Choice([yes_key, no_key], case_sensitive=False),
                default=yes_key
            )
            if answer.lower() != yes_key.lower():
                console_stderr.print("Cancelled")
                sys.exit(1)

        core.ensure_app_dirs()
