# standard library
import sys
import logging
import tomllib
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config, AppBaseConfig
    from pydantic import ValidationError
    import tomllib

# third-party
import rich_click as click

# project
from truenas_api_conduit import COLORS
from truenas_api_conduit.cli_helpers import (
    CLIOptions,
    make_usage_error_panel,
    require_tty,
)

# import truenas_api_conduit.core as core
from truenas_api_conduit.core import CONFIG_PATH, ENV, ensure_config, ensure_storage_dir

from truenas_api_conduit.console import console_stderr

log = logging.getLogger(__name__)

__all__ = [
    "config_setup",
]


def shared_config_setup(cli_options: CLIOptions) -> None:

    if not CONFIG_PATH.exists():
        if not cli_options.api_key or not cli_options.truenas_address:
            log.warning(
                "The config file has not been created yet, it's probably "
                "your first time starting the service. If your server address "
                "and API key are not set yet through other means, the service "
                "will fail to start. You can find the config file through "
                "the config commands."
            )

    # NOTE: Remember that the config file/dir must be ensured before trying to
    # import the user_config module:
    ensure_config()  # Raises if failure
    ensure_storage_dir()


def get_config_args_dict(
    cli_options: CLIOptions, unmask: bool | None = None
) -> dict[str, Any]:

    level_name = logging.getLevelName(logging.getLogger().level)

    # Creating an args dict because we only want to pass in the args that the user
    # passed in through the CLI. You can't pass None values to the Config class because
    # it would treat "None" as the desired value, instead of treating it as missing.

    # NOTE: on the log level: warning is already the default set in the pydantic
    # settings class. If the user didn't pass -v/--verbose, we want to pass None
    # instead of "warning" in order to let pydantic-settings try to pull it from
    # the env var or the config file, before falling back to the default.

    to_filter: dict[str, Any] = {
        "log_level": level_name if level_name.upper() != "WARNING" else None,
        "no_color": cli_options.no_color,
        "start_locked": cli_options.start_locked,
        "conduit_host": cli_options.conduit_host,
    }

    if cli_options.start_locked:
        log.info("Starting in locked mode")

        args_dict = {k: v for k, v in to_filter.items() if v is not None}
        log.debug("Config args: %s", args_dict)
        return args_dict

    else:
        # Remember that start_locked is mutually exclusive with api_key

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

        to_add_to_filter: dict[str, Any] = {
            "truenas_address": cli_options.truenas_address,
            "validate_certs": cli_options.validate_certs,
            "api_key": api_key,
            "crypt_key": cli_options.crypt_key,
        }
        to_filter.update(to_add_to_filter)
        args_dict = {k: v for k, v in to_filter.items() if v is not None}
        log.debug("Config args: %s", args_dict)
        return args_dict


def pretty_print_config(cfg: Config | AppBaseConfig) -> str:

    config_str = ""
    for field, value in cfg.model_dump().items():
        new_section = f"\n{field}: {value}"
        new_section += " " * (37 - len(new_section))
        new_section += f"(from {cfg.provenance[field]})"
        config_str += new_section
    return config_str


def config_setup(cli_options: CLIOptions, unmask: bool | None = None) -> Config:

    # Pydantic will not be loaded until this following import. Its one
    # of the heavier dependencies so this improves startup time marginally.
    from truenas_api_conduit.config import Config

    shared_config_setup(cli_options)
    args_dict = get_config_args_dict(cli_options, unmask)

    try:
        cfg = Config(**args_dict)
    except Exception as e:
        handle_config_error(e)
        sys.exit(1)  # this line will never be reached, but typechecker doesn't know that

    log.info("Config loaded successfully")
    log.info(pretty_print_config(cfg))
    return cfg


def config_setup_locked(cli_options: CLIOptions) -> AppBaseConfig:

    from truenas_api_conduit.config import AppBaseConfig

    shared_config_setup(cli_options)

    args_dict = get_config_args_dict(cli_options)

    try:
        cfg = AppBaseConfig(**args_dict)
    except Exception as e:
        handle_config_error(e)
        sys.exit(1)  # this line will never be reached, but typechecker doesn't know that

    log.info("AppBaseConfig loaded successfully")
    log.info(pretty_print_config(cfg))
    return cfg


def handle_config_error(e: Exception) -> None:

    from truenas_api_conduit.config.file_encrypter import (
        PasswordGetError,
        GetErrorEnum,
    )
    from pydantic import ValidationError

    log_level: int = logging.getLogger().level
    log_mapping = logging.getLevelNamesMapping()

    if isinstance(e, ValidationError):
        _pydantic_error_panel(e)
        sys.exit(1)
    if isinstance(e, tomllib.TOMLDecodeError):
        _toml_decoding_error_panel(e)
        sys.exit(1)
    if isinstance(e, PasswordGetError):
        # This is my custom error class so it will only happen if keyring tried
        # to use my fallback FileEncrypter backend, and the user password was
        # incorrect. Or a bug happened.
        err_string: str | None = None
        if e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
            err_string = "The encryption key you have entered is incorrect."
            console_stderr.print(make_usage_error_panel(err_string, "Keyring Error"))
            sys.exit(1)
        else:
            if log_level <= log_mapping["TRACE"]:
                raise
            else:
                log.error(
                    "Unexpected error: %s | Raise the verbosity to see more information"
                )
                sys.exit(1)
    if isinstance(e, Exception):

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


field_help_dict = {
    "truenas_address": (
        "\n\n[default]You need to set a value for your TrueNAS server's address. You "
        "can set it in one of the following ways:\n"
        "  1. In the config file\n"
        f"  2. As an environment variable [env: [{COLORS.envvar}]{ENV['truenas_address']}[default]=]\n"
        f"  3. Using the [{COLORS.command}]--truenas-address[default] option in the CLI "
        f"(see [{COLORS.command}]start --help[default])"
    ),
    "api_key": (
        "\n\n[default]You need to set a value for your TrueNAS API key. You can set "
        "it in one of the following ways:\n"
        f"  1. Using the [{COLORS.command}]set-key[default] command in the CLI\n"
        f"  2. Using the [{COLORS.command}]--api-key[default] option in the CLI "
        f"(see [{COLORS.command}]start --help[default])\n"
        f"  3. As an environment variable [env: [{COLORS.envvar}]{ENV['api_key']}[default]=]\n"
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
        f"and [{COLORS.command}]config -p[default] commands to edit/find your "
        f"config file.\nUse the [{COLORS.command}]env[default] command to see "
        "the program's environment variables and their current values"
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
