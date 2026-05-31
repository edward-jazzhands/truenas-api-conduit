# standard library
import signal
import sys
import logging
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
from truenas_api_conduit.console import console_stderr, console_stdout

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
    console_stderr.print("\nShutting down.")
    sys.exit(0)


# I used to use the pattern of wrapping the main function in a try/except block
# and looking for KeyboardInterrupt. Turns out that's the noob way to do it,
# the proper way is to register a callback using signal.signal().
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)


MENU_COLORS: dict[str, str] = {
    "command": "deep_sky_blue1",
    "envvar": "orange1",
    "option": "bold cyan",
}

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


def common_setup(cli_options: CLIOptions) -> Config:

    nc_env = os.environ.get("NO_COLOR")
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
    log.info("Logging level currently set to %s", log_level)
    log.info(cli_options)

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
        
        for i, line in enumerate(relevant_lines):
            current_line = (e.lineno-2)+i   
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

        bad_line = doc_split[e.lineno-1]
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
    log.info(cfg)
    provenance_str = "Config provenance:\n\n"
    for field, source in cfg.provenance.items():
        provenance_str += f"  {field}: {source}\n"
    log.debug(cfg.provenance)
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

# GLOBAL OPTIONS

# NOTE: Because these are global options, they will actually run more than once
# on subcommands. This is unfortunately necessary for this pattern to work.
# So in order to prevent that from being an issue, these options will do a
# check to see if the value was already set. If so it will not overwrite it.

def set_verbose_param(ctx: click.Context, param: click.Parameter, value: int) -> int:
    ctx.ensure_object(CLIOptions)
    if ctx.obj.verbose == 0:  #  this means it was not changed yet
        ctx.obj.verbose = value
    return value

def set_no_color_param(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    ctx.ensure_object(CLIOptions)
    if ctx.obj.no_color is None:
        ctx.obj.no_color = value
    return value

# MAIN COMMAND OPTIONS

def set_truenas_host_param(ctx: click.Context, param: click.Parameter, value: str) -> str:
    ctx.ensure_object(CLIOptions)
    ctx.obj.truenas_host = value
    return value

def set_key_param(ctx: click.Context, param: click.Parameter, value: str) -> str:
    ctx.ensure_object(CLIOptions)
    ctx.obj.api_key = value
    return value


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


def main_commands_options(f: Callable) -> Callable:
    f = click.option(
        "--api-key",
        callback=set_key_param,
        is_flag=True,
        default=None,
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



truenas_host_help = f"""The address that you use to access the TrueNAS Web UI over
HTTPS. You can also set the [{MENU_COLORS['envvar']}]truenas_host[/{MENU_COLORS['envvar']}]
field in the config file, or set an environment variable named
[{MENU_COLORS['envvar']}]TRUENAS_HOST[/{MENU_COLORS['envvar']}]"""

api_key_help = f"""Ask to be prompted for your TrueNAS API key. You can also use the
[{MENU_COLORS['command']}]set-key[/{MENU_COLORS['command']}] command (recommended),
set an environment variable named
[{MENU_COLORS['envvar']}]TRUENAS_API_KEY[/{MENU_COLORS['envvar']}], or set the
[{MENU_COLORS['envvar']}]api_key[/{MENU_COLORS['envvar']}] field in the config file"""

verbose_help = f"""Sets the verbosity/logging level.
[{MENU_COLORS['option']}]-v[/{MENU_COLORS['option']}] for info,
[{MENU_COLORS['option']}]-vv[/{MENU_COLORS['option']}] for debug,
[{MENU_COLORS['option']}]-vvv[/{MENU_COLORS['option']}] for trace"""

no_color_help = f"""Disables color output. You can also set the
[{MENU_COLORS['envvar']}]NO_COLOR[/{MENU_COLORS['envvar']}] environment variable to fully
disable color including the help menu"""



# NOTE: When using click.group() as the main command, it will automatically show
# the --help message when no subcommands are specified.


class CustomGroup(DYMMixin, click.RichGroup):  # Adds click-didyoumean
    pass


main_commands = [
    "request",
    "install",
    "start",
    "stop",
    "restart",
    "status",
    "uninstall",
]

config_commands = [
    "set_key",
    "config",
    "config_path",
    "print_config",
]

global_options = [
    "verbose",
    "no_color",
]


context = {
    "rich_console": console_stdout,
}

@click.group(cls=CustomGroup, context_settings=context)
@click.command_panel("Commands", commands=main_commands)
@click.command_panel("Config", commands=config_commands)
@common_options
@click.pass_context
def cli(ctx: click.RichContext) -> None:
    """TrueNAS API Conduit - A websocket proxy service for the TrueNAS API.

    This will hold the websocket connection open so that subsequent requests can
    re-use the same connection. It can be installed as a service, or run as a
    standalone program without installing"""

    # NOTE: having the common_options decorator on the main group means those 
    # options are visible in the main help menu which is important for UX. It
    # also means a user can apply a global option to the main command
    # (as you can typically do with Click-based apps), like so:
    #    1) >>> truenas-api -vv start
    # as well as:
    #    2) >>> truenas-api start -vv

    # Click normally forces passing global options to the main command (like #1)
    # but my system allows you to additionally use style #2. The options will
    # show up in the main help menu as well as the individual help menus for
    # each command. Rich-Click helps a lot for making this look nice with
    # the command_panel decorators (above).

    # But we do not want to run the common_setop function here, because it
    # does the full initialization of the config and logging. Some commands
    # do not require this.


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
    ctx: click.RichContext,
    system: bool = False,
    package: bool = False,
) -> None:
    """Install the TrueNAS API Conduit service. On Linux and MacOS, the default
    is to install as a user service and does not require elevation. On Windows,
    elevation is required to install"""

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
def uninstall(ctx: click.RichContext) -> None:
    """Uninstall the conduit service"""
    pass


foreground_help = """Starts the service as a standalone program in the foreground (not
run by your service manager). Does not require installation"""

start_help = f"""Tell your OS to start the conduit service. You can also
start the program directly as a standalone program without installing by using the
[{MENU_COLORS['option']}]--standalone[/{MENU_COLORS['option']}] option, which runs in
the foreground by default. Tip: to run standalone in the background, use:
[{MENU_COLORS['command']}]truenas-api start & disown[/{MENU_COLORS['command']}]
(Mac + Linux) or
[{MENU_COLORS['command']}]Start-Process truenas-api start[/{MENU_COLORS['command']}]
(Windows)"""

@cli.command(help=start_help)
@click.option("--standalone", is_flag=True, default=False, help=foreground_help)
@main_commands_options
@common_options
@click.pass_context
def start(ctx: click.RichContext, standalone: bool) -> None:

    if ctx.obj.verbose > 1:
        if ctx.obj.no_color:
            click.echo(ctx.obj)
        else:
            console_stderr.print(ctx.obj)

    assert isinstance(ctx.obj, CLIOptions)
    cfg = common_setup(ctx.obj)

    if standalone:
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

request_help = f"""Make a request using the service. The service must be running.\n
Example: [{MENU_COLORS['command']}]truenas-api request system.info[/{MENU_COLORS['command']}]
"""

@cli.command(help=request_help)
@click.argument("method", help="The method to call (ex: system.info)", required=True)
@click.option("--params", "-p", help="The params to pass to the method")
@main_commands_options
@common_options
@click.pass_context
def request(
    ctx: click.RichContext,
    method: str,
    params: str | None = None,
) -> None:
    
    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cfg = common_setup(ctx.obj)

    log.debug("Making request")
    import requests
    import json
    import yaspin

    params_list: list[Any] = []
    if params:
        log.debug("Method: %s | Params: %s", method, params)
        try:
            params_list = json.loads(params)
        except json.JSONDecodeError as e:
            raise click.UsageError(f"Malformed params: {e}")

    with yaspin.yaspin(text="Sending request..."):
        response = requests.post(
            f"http://127.0.0.1:{cfg.socket_port}/rpc",
            json={"method": method, "params": params_list}
        )
    ctx.console.print(response.json())


@cli.command()
@common_options
@click.pass_context
def stop(ctx: click.RichContext) -> None:
    """Stop the conduit service"""
    pass


@cli.command()
@common_options
@click.pass_context
def restart(ctx: click.RichContext) -> None:
    """Restart the conduit service"""
    pass


@cli.command()
@common_options
@click.pass_context
def status(ctx: click.RichContext) -> None:
    """Check the status of the conduit service"""

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cfg = common_setup(ctx.obj)

    log.debug("Making request")
    import requests

    response = requests.post(f"http://127.0.0.1:{cfg.socket_port}/status")
    click.echo(response.json())


@cli.command()
@common_options
@click.pass_context
def set_key(ctx: click.RichContext) -> None:
    """Sets the API key using whatever compatible keyring/secrets manager is
    available on your system"""

    log.debug("Setting API key")
    import keyring

    # TODO: Implement set API key

config_help = f"""Attempts to open the config file in your editor, if
[{MENU_COLORS['envvar']}]$EDITOR[/{MENU_COLORS['envvar']}] is set"""

@cli.command(help=config_help)
@common_options
@click.pass_context
def config(ctx: click.RichContext) -> None:
    
    editor = os.environ.get("EDITOR")
    if not editor:
        raise click.UsageError("No editor set. Set the $EDITOR environment variable")
    os.execvp(editor, [editor, core.CONFIG_PATH])


@cli.command()
@common_options
@click.pass_context
def config_path(ctx: click.RichContext) -> None:
    """Prints the path to the config file"""

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
def print_config(ctx: click.RichContext) -> None:
    """Validates and outputs your current configuration as JSON to stdout.
    Logging/debug is separated out to stderr"""

    assert isinstance(ctx.obj, CLIOptions)
    cfg = common_setup(ctx.obj)

    json_dict = cfg.model_dump_json(indent=2)
    click.echo(json_dict)
    if ctx.obj.verbose == 0:
        console_stderr.print(
            f"\n[italic]Tip: You can increase the verbosity to see provenance[/italic]",
            markup=True,
        )
