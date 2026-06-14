# standard library
import sys
import logging
import os
from typing import Any, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config
    from pydantic import ValidationError
    import tomllib

# third-party
import rich_click as click
from rich.panel import Panel

# project
from truenas_api_conduit import log_setup
from truenas_api_conduit import COLORS
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

    log_setup.init_logging()

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

    log_setup.set_log_level(log_level)


def config_setup(cli_options: CLIOptions, unmask: bool | None = None) -> Config:

    log_level: int = logging.getLogger().level
    level_name = logging.getLevelName(log_level)
    log_mapping = logging.getLevelNamesMapping()

    if not core.CONFIG_PATH.exists():
        if not cli_options.api_key or not cli_options.truenas_host:
            log.warning(
                "The config file has not been created yet, it's probably "
                "your first time starting the service. If your server address "
                "and API key are not set yet through other means, the service "
                "will fail to start. You can find the config file through "
                "the config commands."
            )

    # NOTE: Remember that the config file/dir must be ensured before trying to
    # import the user_config module:
    core.ensure_config()  # Raises if failure
    core.ensure_storage_dir()

    # Pydantic will not be loaded until this following import. Its one
    # of the heavier dependencies so this improves startup time marginally.
    from truenas_api_conduit.config import Config
    from truenas_api_conduit.config.file_encrypter import (
        PasswordGetError,
        GetErrorEnum,
    )
    from pydantic import ValidationError
    import tomllib

    # only used by the start command
    if cli_options.api_key:
        log.debug("Prompting for API key")
        require_tty("API key")
        api_key = click.prompt("Enter your TrueNAS API key", hide_input=True)
    else:
        api_key = None

    # only used by the print-config command
    # NOTE: The point of this is so that the user won't be triggered to enter
    # the password for their keyring/secret manager unless they set --unmask.
    # It just tricks pydantic into thinking the API key was passed in through
    # CLI args and thus will skip the keyring/secret manager.
    if unmask is False:
        api_key = "*" * 10

    # Creating an args dict because we only want to pass in the args that the user
    # passed in through the CLI. You can't pass None values to the Config class because
    # it would treat "None" as the desired value, instead of treating it as missing.
    to_filter: dict[str, Any] = {
        "log_level": level_name if level_name.upper() != "WARNING" else None,
        "no_color": cli_options.no_color,
        "truenas_host": cli_options.truenas_host,
        "api_key": api_key,
    }
    args_dict = {k: v for k, v in to_filter.items() if v is not None}

    log.debug("Config args: %s", args_dict)

    # NOTE: on the log level: warning is already the default set in the pydantic
    # settings class. If the user didn't pass -v/--verbose, we want to pass None
    # instead of "warning" in order to let pydantic-settings try to pull it from
    # the env var or the config file, before falling back to the default.

    try:
        cfg = Config(**args_dict)
    except ValidationError as e:
        _pydantic_error_panel(e)
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        _toml_decoding_error_panel(e)
        sys.exit(1)
    except PasswordGetError as e:
        # This is my custom error class so it will only happen if keyring tried
        # to use my fallback FileEncrypter backend, and the user password was
        # incorrect. Or a bug happened.
        err_string: str | None = None
        if e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
            err_string = "The encryption key you have entered is incorrect."
            console_stderr.print(make_usage_error_panel(err_string, "Keyring Error"))
            sys.exit(1)
        else:
            if cli_options.verbose >= 3:
                raise
            else:
                log.error(
                    "Unexpected error: %s | Raise the verbosity to see more information"
                )
                sys.exit(1)
    except Exception as e:
        if log_level <= log_mapping["TRACE"]:
            raise
        else:
            err_string = (
                "[default]Could not initialize config:\n\n"
                f"    {e} ({e.__class__.__qualname__})\n\n"
                "Raise the verbosity to see more information."
            )
            console_stderr.print(make_usage_error_panel(err_string))
            sys.exit(1)

    log.info("Config loaded successfully")
    config_str = ""
    for field, value in cfg.model_dump().items():
        new_section = f"\n{field}: {value}"
        new_section += " " * (35 - len(new_section))
        new_section += f"(from {cfg.provenance[field]})"
        config_str += new_section
    log.info(config_str)
    return cfg


field_help_dict = {
    "truenas_host": (
        "\n\n[default]You need to set a value for your TrueNAS server's address. You "
        "can set it in one of the following ways:\n"
        "  1. In the config file\n"
        f"  2. As an environment variable [env: [{COLORS.envvar}]TRUENAS_HOST[default]=]\n"
        f"  3. Using the [{COLORS.command}]--truenas-host[default] option in the CLI "
        f"(see [{COLORS.command}]start --help[default])"
    ),
    "api_key": (
        "\n\n[default]You need to set a value for your TrueNAS API key. You can set "
        "it in one of the following ways:\n"
        f"  1. Using the [{COLORS.command}]set-key[default] command in the CLI\n"
        f"  2. Using the [{COLORS.command}]--api-key[default] option in the CLI "
        f"(see [{COLORS.command}]start --help[default])\n"
        f"  3. As an environment variable [env: [{COLORS.envvar}]TRUENAS_API_KEY[default]=]\n"
        "  4. In the config file (least secure)"
    ),
}


def _pydantic_error_panel(e: ValidationError) -> None:

    fields_with_errors: list[str | int] = []

    errs = e.errors()
    err_string = "[default]The following errors were found in your configuration:"
    for err in errs:
        field_name = err["loc"][0]
        if isinstance(field_name, str):
            field_name = field_name.lower()
        fields_with_errors.append(field_name)
        err_string += f"\n    [yellow]{field_name}[default] is {err['type']}:  "
        err_string += f"[bright_red]{err['msg']}"
    for field_name in fields_with_errors:
        if field_name in field_help_dict:
            err_string += field_help_dict[field_name]
    err_string += (
        f"\n\n[default]You can use the [{COLORS.command}]config[default] "
        f"and [{COLORS.command}]config-path[default] commands to edit/find your "
        "config file"
    )
    console_stderr.print(make_usage_error_panel(err_string, "Configuration Errors"))


def _toml_decoding_error_panel(e: tomllib.TOMLDecodeError) -> None:

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
            err_string += ">>> "
        else:
            err_string += "    "
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
        err_string += '\nOnly found one doublequote(") mark in the line. '
        err_string += "Did you forget to close it?\n"
    if bad_line.count("'") == 1:
        err_string += "\nOnly found one singlequote(') mark in the line. "
        err_string += "Did you forget to close it?\n"

    console_stderr.print(make_usage_error_panel(err_string))


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
