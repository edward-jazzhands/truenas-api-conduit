# standard library
import signal
import sys
import logging
import subprocess
import os
from typing import Any, Callable, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

# third-party
from rich.panel import Panel
from rich.style import Style
import rich_click as click
from click_didyoumean import DYMMixin
from rich.traceback import install as tb_install

# project
from truenas_api_conduit import __version__, APP_NAME, log_setup
import truenas_api_conduit.core as core
from truenas_api_conduit.console import console_stderr

# rich tracebacks
tb_install(console=console_stderr, show_locals=False)

log = logging.getLogger(__name__)

# Rich-click Config
click.rich_click.MAX_WIDTH = 120
click.rich_click.COMMANDS_BEFORE_OPTIONS = True
click.rich_click.THEME = "cargo-modern"
click.rich_click.USE_RICH_MARKUP = True
# colorschemes: #~ [default, star, quartz, quartz2, cargo, forest, nord, dracula, solarized]
# theme types: #~ [box, slim, modern, robo, nu]
# nord, dracula, and solarized are "risky" according to the docs.


def handle_exit(*_):
    print("\nShutting down.")
    sys.exit(0)


# I used to use the pattern of wrapping the main function in a try/except block
# and looking for KeyboardInterrupt. Turns out that's the noob way to do it,
# the proper way is to register a callback using signal.signal().
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)


@dataclass
class CLIOptions:
    """dataclass\n
    ```
    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    """

    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    no_color: bool | None = None


def common_setup(cli_options: CLIOptions) -> Config:

    nc_env = os.environ.get("NO_COLOR")

    print(nc_env)
    print(cli_options.no_color)


    if nc_env is not None or cli_options.no_color:
        console_stderr.no_color = True

    log_setup.init_logging()

    # Remember the root logger starts at WARNING or ERROR
    log_mapping = logging.getLevelNamesMapping()
    level_name: str | None = None

    if cli_options.verbose > 0:
        if cli_options.verbose == 1:
            log_level = log_mapping["INFO"]  # 20
        elif cli_options.verbose == 2:
            log_level = log_mapping["DEBUG"]  # 10
        else:
            log_level = log_mapping["TRACE"]  # 5

        level_name = logging.getLevelName(log_level)
        log_setup.set_log_level(log_level)

    log_level: int = logging.getLogger().level
    log.info("Logging level set to %s", log_level)

    # Creating an args dict because we only want to pass in the args that the user
    # passed in through the CLI. You can't pass None values to the Config class because
    # it would treat "None" as the desired value, instead of treating it as missing.
    to_filter: dict[str, Any] = {
        "log_level": level_name,
        "no_color": cli_options.no_color,
        "truenas_host": cli_options.truenas_host,
        "api_key": cli_options.api_key,
    }
    args_dict = {k: v for k, v in to_filter.items() if v is not None}

    # NOTE: Remember that the config file/dir must be ensured before trying to
    # import the user_config module:
    core.ensure_config()  # Raises if failure

    # Pydantic will not be loaded until this following import. Its one
    # of the heavier dependencies so this improves startup time.
    from truenas_api_conduit.config import Config
    from pydantic import ValidationError  # .config already imports pydantic
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
        err_string = (
            "[default]Your config file could not be parsed due to a TOML syntax error "
            f"at line {e.lineno}:\n\n"
        )
        doc_split = e.doc.splitlines()
        relevant_lines = doc_split[e.lineno-3:e.lineno+2]
        bad_line = doc_split[e.lineno-1]
        for i, line in enumerate(relevant_lines):
            err_string += f"{(e.lineno-2)+i} | "
            if line.strip().startswith("#"):
                err_string += f"[gray50]{line}[/gray50]\n"
            else:
                err_string += f"[bright_yellow]{line}[/bright_yellow]\n"
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
    log.debug(cfg)
    provenance_str = "Config provenance:\n"
    for field, source in cfg.provenance.items():
        provenance_str += f"{field}: {source}\n"
    log.info(provenance_str)
    return cfg


# === Click Option Callbacks ===

# NOTE: The click option callback pattern
# https://click.palletsprojects.com/en/stable/advanced/#callbacks

# Normally option callbacks are used for validation and similar tasks.
# I'm using them in a somewhat unconventional manner here, which is to grab
# individual options the user passed in through the CLI and set them as attributes
# on the CLIOptions dataclass.
# The reason for this is entirely because of wanting to have "shared options".
# Normally Click is designed so that shared options would need to be passed in
# to the main command, with subcommands coming *after* the options, like this:
#    $ truenas-api --api-key=1234567890 start

# This, I believe, is awkward and not how most other CLI frameworks handle this.
# Instead I want these options to be available to all subcommands, like this:
#    $ truenas-api start --api-key=1234567890

# In order to achieve this, we need to use these callbacks combined with custom
# option group decorators (below), which we can then re-use across subcommands.
# I researched all the possible ways to solve this problem, and this seems to be
# the most recommended one.


def set_verbose_param(ctx: click.Context, param: click.Parameter, value: int) -> int:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.verbose = value
    return value

def set_no_color_param(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.no_color = value
    return value

def set_truenas_host_param(ctx: click.Context, param: click.Parameter, value: str) -> str:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.truenas_host = value
    return value

def set_key_param(ctx: click.Context, param: click.Parameter, value: str) -> str:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.api_key = value
    return value


verbose_help = """Sets the verbosity/logging level. -v for info, \
-vv for debug, -vvv for trace"""

no_color_help = """Disables color output. You can also set the NO_COLOR environment variable."""


def common_options(f: Callable) -> Callable:
    f = click.option(
        "-v",
        "--verbose",
        count=True,
        callback=set_verbose_param,
        expose_value=False,  # * <-- This is important
        help=verbose_help,
    )(f)
    f = click.option(
        "-nc",
        "--no-color",
        is_flag=True,
        default=None,
        callback=set_no_color_param,
        expose_value=False, 
        help=no_color_help,
    )(f)
    return f

    # NOTE: I don't usually do syntax notes but this one is tricky.
    # f = click.option(args)(f)   <- click.option returns a decorator
    # Remember every step in the decorator chain takes a function and
    # then returns a new wrapped function. We're taking our previous function
    # in the decorator chain ('f') and passing it into whatever function was
    # returned by click.option.


truenas_host_help = """The address that you use to access the TrueNAS Web UI over HTTPS.
You can also set the [orange1]truenas_host[/orange1] field in the config file, or set an
environment variable named [orange1]TRUENAS_HOST[/orange1]."""

api_key_help = """Your TrueNAS API key. You can also use the
[deep_sky_blue1]set-key[/deep_sky_blue1] command (recommended), set an environment variable
named [orange1]TRUENAS_API_KEY[/orange1], or set the [orange1]api_key[/orange1] field
 in the config file."""

def main_commands_options(f: Callable) -> Callable:
    f = click.option(
        "--api-key",
        callback=set_key_param,
        expose_value=False,
        help=api_key_help,
    )(f)
    f = click.option(
        "--truenas-host",
        callback=set_truenas_host_param,
        expose_value=False,
        help=truenas_host_help,
    )(f)
    return f


# NOTE: When using click.group() as the main command, it will automatically show
# the --help message when no subcommands are specified.


class CustomGroup(DYMMixin, click.RichGroup):  # Adds click-didyoumean
    pass


main_commands = [
    "request",
    "start",
    "stop",
    "restart",
    "status",
    "install",
    "uninstall",
]

config_commands = [
    "set_key",
    "config",
    "config_path",
    "print_config",
]


@click.group(cls=CustomGroup)
@click.command_panel("Main", commands=main_commands)
@click.command_panel("Config", commands=config_commands)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """TrueNAS API Conduit - A websocket proxy service for the TrueNAS API.

    This will hold the websocket connection open so that subsequent requests can
    re-use the same connection. It can be installed as a service, or run as a
    standalone program without installing."""

    ctx.ensure_object(CLIOptions)


system_help = """Installs the service as a system service. This requires elevation"""
package_help = """This is intended to be used by package managers"""


@cli.command()
@click.option("--system", "-s", is_flag=True, default=False, help=system_help)
@click.option(
    "--package", "-p", is_flag=True, default=False, help=package_help, hidden=True
)
@common_options
@click.pass_context
def install(
    ctx: click.Context,
    system: bool = False,
    package: bool = False,
) -> None:
    """Install the TrueNAS API Conduit service. On Linux and MacOS, the default
    is to install as a user service and does not require elevation. On Windows,
    elevation is required to install.
    """

    if system and package:
        raise click.UsageError("You cannot specify both --system and --package")

    assert isinstance(ctx.obj, CLIOptions)
    cfg = common_setup(ctx.obj)

    

    from truenas_api_conduit.service import get_service_manager
    from truenas_api_conduit.core import PLATFORM, InstallType

    service = get_service_manager(PLATFORM)

    if system:
        service.install(InstallType.SYSTEM)
    elif package:
        service.install(InstallType.PACKAGE)
    else:
        service.install(InstallType.USER)


@cli.command()
@common_options
@click.pass_context
def uninstall(ctx: click.Context) -> None:
    """Uninstall the TrueNAS API Conduit service."""
    pass


foreground_help = """Starts the service as a standalone program in the foreground (not
run by your service manager). This is useful for debugging and development"""


@cli.command()
@main_commands_options
@click.option("--foreground", "-fg", is_flag=True, default=False, help=foreground_help)
@common_options
@click.pass_context
def start(ctx: click.Context, foreground: bool) -> None:
    """Tells your OS to start the TrueNAS API Conduit service. You can also start
    the program directly as a standalone program without installing by using the
    --foreground option."""

    assert isinstance(ctx.obj, CLIOptions)
    cfg = common_setup(ctx.obj)

    if foreground:
        log.info("Starting service in foreground")

        os.environ["TAC_CONFIG"] = cfg.model_dump_json()
        dname = "truenas-api-conduitd"
        os.execvp(dname, [dname])

    else:
        log.info("Telling OS to start the service")
        # from truenas_api_conduit.service import get_service_manager
        # from truenas_api_conduit.core import PLATFORM
        # service = get_service_manager(PLATFORM)

        # service.start(cfg)


@cli.command()
@common_options
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop the TrueNAS API Conduit service."""
    pass


@cli.command()
@common_options
@click.pass_context
def restart(ctx: click.Context) -> None:
    """Restart the TrueNAS API Conduit service."""
    pass


@cli.command()
@common_options
@click.pass_context
def status(ctx: click.Context) -> None:
    """Check the status of the TrueNAS API Conduit service."""
    pass


@cli.command()
@main_commands_options
@common_options
@click.pass_context
def request(ctx: click.Context) -> None:
    """Make a request, using the service if it's running. Otherwise, the program
    will open a websocket connection, make the request, and close the connection."""

    # TODO: Implement request
    log.debug("Making request")
    log.debug("Context: %s", ctx.obj.__dict__)
    pass


@cli.command()
@common_options
@click.pass_context
def set_key(ctx: click.Context) -> None:
    """Sets the API key using whatever compatible keyring/secrets manager is
    available on your system."""

    log.debug("Setting API key")
    import keyring

    # TODO: Implement set API key


@cli.command()
@common_options
@click.pass_context
def config(ctx: click.Context) -> None:
    """Attempts to open the config file in your editor, if $EDITOR is set."""

    editor = os.environ.get("EDITOR")
    if not editor:
        raise click.UsageError("No editor set. Set the $EDITOR environment variable.")
    os.execvp(editor, [editor, core.CONFIG_PATH])


@cli.command()
@common_options
@click.pass_context
def config_path(ctx: click.Context) -> None:
    """Prints the path to the config file."""

    click.echo(core.CONFIG_PATH)  # stays clean/pure for piping
    console_stderr.print(f"Created already?: {core.CONFIG_PATH.exists()}")
    console_stderr.print(
        f"[italic]Tip: You can pipe this command into an editor:[/italic]"
        "  [yellow]nano $(truenas-api config-path)[/yellow]",
        markup=True,
    )


@cli.command()
@common_options
@click.pass_context
def print_config(ctx: click.Context) -> None:
    """Outputs your current configuration as JSON to stdout. Logging/debug
    is separated out to stderr"""

    assert isinstance(ctx.obj, CLIOptions)
    cfg = common_setup(ctx.obj)

    json_dict = cfg.model_dump_json(indent=2)
    click.echo(json_dict)
    if ctx.obj.verbose == 0:
        console_stderr.print(
            f"\n[italic]Tip: You can increase the verbosity to see provenance[/italic]",
            markup=True,
        )
