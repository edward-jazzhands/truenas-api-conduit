# standard library
import sys
import logging
import os
import json
from typing import Any, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config
    import tomllib

# third-party
import rich_click as click

# project
from truenas_api_conduit import log_setup
import truenas_api_conduit.core as core
from truenas_api_conduit.console import console_stderr, set_no_color

log = logging.getLogger(__name__)

__all__ = [
    "CLIOptions",
    "logging_setup",
    "config_setup",
]


@dataclass
class CLIOptions:
    """dataclass\n
    ```
    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    no_color: bool | None = None
    """

    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    no_color: bool | None = None


def logging_setup(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)

    nc_env = os.environ.get("NO_COLOR")
    if nc_env is not None or ctx.obj.no_color:
        set_no_color()

    if ctx.obj.verbose > 1:
        console_stderr.print(ctx.obj)

    log_setup.init_logging()

    log_mapping = logging.getLevelNamesMapping()
    log_level: int = logging.getLogger().level  # starts at WARNING

    if ctx.obj.verbose > 0:
        if ctx.obj.verbose == 1:
            log_level = log_mapping["INFO"]  # 20
        elif ctx.obj.verbose == 2:
            log_level = log_mapping["DEBUG"]  # 10
        else:
            log_level = log_mapping["TRACE"]  # 5

    log_setup.set_log_level(log_level)


def config_setup(cli_options: CLIOptions) -> Config:

    log_level: int = logging.getLogger().level
    level_name = logging.getLevelName(log_level)
    log_mapping = logging.getLevelNamesMapping()

    if cli_options.api_key:
        log.debug("Prompting for API key")
        api_key = click.prompt("Enter your TrueNAS API key", hide_input=True)
    else:
        api_key = None

    # Creating an args dict because we only want to pass in the args that the user
    # passed in through the CLI. You can't pass None values to the Config class because
    # it would treat "None" as the desired value, instead of treating it as missing.
    to_filter: dict[str, Any] = {
        "log_level": level_name,
        "no_color": cli_options.no_color,
        "truenas_host": cli_options.truenas_host,
        "api_key": api_key,
    }
    args_dict = {k: v for k, v in to_filter.items() if v is not None}

    # NOTE: Remember that the config file/dir must be ensured before trying to
    # import the user_config module:
    core.ensure_config()  # Raises if failure

    # Pydantic will not be loaded until this following import. Its one
    # of the heavier dependencies so this improves startup time.
    from truenas_api_conduit.config import Config
    from pydantic import ValidationError  # .config already imports pydantic
    from rich.panel import Panel
    import tomllib

    try:
        cfg = Config(**args_dict)
    except ValidationError as e:
        errs = e.errors()
        err_string = "[default]The following errors were found in your configuration:"
        for err in errs:
            err_string += f"\n    [yellow]{err['loc'][0]}[/yellow] is {err['type']}:  "
            err_string += f"[bright_red]{err['msg']}"
        console_stderr.print(
            Panel(
                err_string,
                title="Configuration Errors",
                style="red",
                title_align="left",
            )
        )
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        _toml_decoding_error_panel(e)
        sys.exit(1)
    except Exception as e:
        if log_level <= log_mapping["TRACE"]:
            raise
        elif log_level <= log_mapping["DEBUG"]:
            log.exception(
                f"Could not initialize config. Raise level to -vvv (trace) "
                "to see the full traceback."
            )
            sys.exit(1)
        else:
            err_string = (
                "[default]Could not initialize config:\n\n"
                f"    {e} ({e.__class__.__qualname__})\n\n"
                "Raise the verbosity to see more information."
            )
            console_stderr.print(Panel(err_string, style="red"))
            sys.exit(1)

    log.info("Config loaded successfully")
    log.info(cfg)
    provenance_str = "Config provenance:\n\n"
    for field, source in cfg.provenance.items():
        provenance_str += f"  {field}: {source}\n"
    log.debug(cfg.provenance)
    return cfg


def _toml_decoding_error_panel(e: tomllib.TOMLDecodeError) -> None:

    from rich.panel import Panel

    err_string = (
        "[default]Your config file could not be parsed due to a TOML syntax error "
        f"at line {e.lineno}:\n\n"
    )
    doc_split = e.doc.splitlines()
    relevant_lines = doc_split[e.lineno - 3 : e.lineno + 2]

    for i, line in enumerate(relevant_lines):
        current_line = (e.lineno - 2) + i
        is_bad_line = False

        if current_line == e.lineno:
            is_bad_line = True
            err_string += f">>> "
        else:
            err_string += f"    "
        if current_line <= 9:
            err_string += " "

        err_string += f"{current_line} | "

        if line.strip().startswith("#"):
            err_string += f"[gray50]{line}[/gray50]\n"
        elif is_bad_line:
            err_string += f"[bright_yellow]{line}[/bright_yellow]\n"
        else:
            err_string += f"{line}\n"

    # Error help/suggestions

    bad_line = doc_split[e.lineno - 1]
    for word in ["True", "False"]:
        if word in bad_line:
            err_string += f"\nYou used '{word}' with a capital {word[0]}. "
            err_string += f"This must be lowercase like '{word.lower()}'.\n"
    if bad_line.count('"') == 1:
        err_string += f'\nOnly found one doublequote(") mark in the line. '
        err_string += f"Did you forget to close it?\n"
    if bad_line.count("'") == 1:
        err_string += f"\nOnly found one singlequote(') mark in the line. "
        err_string += f"Did you forget to close it?\n"
    if bad_line.count("'") == 0 and bad_line.count('"') == 0:
        err_string += "\nTip: does it need to be enclosed in quotes?\n"

    console_stderr.print(Panel(err_string, style="red"))
