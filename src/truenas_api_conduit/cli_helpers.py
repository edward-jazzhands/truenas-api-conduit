# standard library
import sys
import logging
import os
from dataclasses import dataclass

# third-party
import rich_click as click
from rich.panel import Panel

# project
from truenas_api_conduit import logging_manager
import truenas_api_conduit.core as core
from truenas_api_conduit.console import console_stderr, set_no_color

log = logging.getLogger(__name__)

__all__ = [
    "CLIOptions",
    "logging_setup",
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
    no_color: bool | None = None
    pretty: bool | None = None


def make_usage_error_panel(err_string: str, title: str = "Error") -> Panel:
    err_string = "[default]" + err_string
    return Panel(err_string, title=title, title_align="left", style="bright_red")


def make_success_panel(msg: str, title: str = "Success") -> Panel:
    msg = "[default]" + msg
    return Panel(msg, title=title, title_align="left", style="bright_green")


def require_tty(prompt_description: str, additional: str = "") -> None:
    if not sys.stdin.isatty():
        console_stderr.print(
            f"Cannot prompt for {prompt_description}: stdin is not a TTY.",
        )
        if additional:
            console_stderr.print(additional)
        sys.exit(1)


def logging_setup(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)

    nc_env = os.environ.get("NO_COLOR")
    if nc_env is not None or ctx.obj.no_color:
        set_no_color()

    if ctx.obj.verbose > 1:
        console_stderr.print(ctx.obj)

    logging_manager.init_logging()

    log_mapping = logging.getLevelNamesMapping()
    log_level: int = logging.getLogger().level  # starts at WARNING

    log_env = os.environ.get("LOG_LEVEL")
    if log_env:
        log_level = log_mapping[log_env.upper()]

    # If verbosity is set, it overrides the env var
    if ctx.obj.verbose > 0:
        if ctx.obj.verbose == 1:
            log_level = log_mapping["INFO"]  # 20
        elif ctx.obj.verbose == 2:
            log_level = log_mapping["DEBUG"]  # 10
        else:
            log_level = log_mapping["TRACE"]  # 5

    logging_manager.set_log_level(log_level)


def prompt_for_config() -> None:
    """Used in commands that need to ensure the config dir exists, but without
    triggering the full pydantic config validation: set-key, and config. In case
    the user tries to open/read the config file before they've run the program
    for the first time."""

    if not core.CONFIG_DIR.exists():

        if sys.stdin.isatty():
            console_stderr.print(
                "The config directory has not been created yet. Do you want "
                "to create it now? (y/n)"
            )
            answer = click.prompt("Enter 'y' to create the config directory")
            if answer.lower() not in ("y", "yes"):
                console_stderr.print("Cancelled")
                sys.exit(1)

        core.ensure_config()

    if not core.CONFIG_PATH.exists():
        if sys.stdin.isatty():

            console_stderr.print(
                "The config file is missing. Do you want to create a new one "
                "with the default settings? (y/n)"
            )
            answer = click.prompt("Enter 'y' to create the config file")
            if answer.lower() not in ("y", "yes"):
                console_stderr.print("Cancelled")
                sys.exit(1)
        core.ensure_config()
