# standard library
import signal
import sys
import logging
import os
import json
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config
    from truenas_api_conduit.request_helper import RequestHelper

# third-party
import rich_click as click
from click_didyoumean import DYMMixin
from rich.traceback import install as tb_install

# project
from truenas_api_conduit import __version__, APP_NAME
import truenas_api_conduit.core as core
from truenas_api_conduit.console import console_stderr, console_stdout
from truenas_api_conduit.cli_helpers import CLIOptions, logging_setup, config_setup
from truenas_api_conduit.request_helper import get_request_helper

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

    ctx.ensure_object(CLIOptions)

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

    # However, the setup functions (logging_setup, config_setup) cannot be
    # run here, because they would not catch options that were passed into
    # the subcommands. If global options are set on the main command, they'll
    # be passed through so that the subcommand setup gets the full context.


start_help = f"""Tell your OS to start the conduit service. You can also
start the program directly as a standalone program without installing by using the
[{MENU_COLORS['option']}]--standalone[/{MENU_COLORS['option']}] option, which runs in
the foreground by default. Tip: to run standalone in the background, use:
[{MENU_COLORS['command']}]truenas-api start --standalone & disown[/{MENU_COLORS['command']}]
(Mac + Linux) or
[{MENU_COLORS['command']}]Start-Process truenas-api start
--standalone[/{MENU_COLORS['command']}] (Windows)"""

standalone_help = """Starts the service as a standalone program in the foreground (not
run by your service manager). Does not require installation"""

api_key_help = f"""Ask to be prompted for your TrueNAS API key. You can also use the
[{MENU_COLORS['command']}]set-key[/{MENU_COLORS['command']}] command (recommended),
set an environment variable named
[{MENU_COLORS['envvar']}]TRUENAS_API_KEY[/{MENU_COLORS['envvar']}], or set the
[{MENU_COLORS['envvar']}]api_key[/{MENU_COLORS['envvar']}] field in the config file"""

truenas_host_help = f"""The address that you use to access the TrueNAS Web UI over
HTTPS. You can also set the [{MENU_COLORS['envvar']}]truenas_host[/{MENU_COLORS['envvar']}]
field in the config file, or set an environment variable named
[{MENU_COLORS['envvar']}]TRUENAS_HOST[/{MENU_COLORS['envvar']}]"""


@cli.command(help=start_help)
@click.option("--standalone", is_flag=True, default=False, help=standalone_help)
@click.option("--api-key", is_flag=True, default=None, help=api_key_help)
@click.option("--truenas-host", help=truenas_host_help)
@common_options
@click.pass_context
def start(
    ctx: click.RichContext,
    standalone: bool,
    api_key: bool | None = None,
    truenas_host: str | None = None,
) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    ctx.obj.api_key = api_key
    ctx.obj.truenas_host = truenas_host
    cfg = config_setup(ctx.obj)

    if standalone:
        log.info("Starting service in foreground")

        # * This shall henceforth be known as The execvp Chad Swap inside my brain

        os.environ["TAC_CONFIG"] = cfg.model_dump_json()
        dname = APP_NAME + "d"  # ex: my-appd
        os.execvp(dname, [dname])
    else:
        log.info("Telling OS to start the service")
        # from truenas_api_conduit.service import get_service_manager
        # from truenas_api_conduit.core import PLATFORM
        # service = get_service_manager(PLATFORM)

        # service.start(cfg)


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

    logging_setup(ctx)
    assert ctx.console is not None

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

    logging_setup(ctx)
    assert ctx.console is not None


request_help = f"""Make a request using the service. The service must be running.\n
Example: [{MENU_COLORS['command']}]truenas-api request system.info[/{MENU_COLORS['command']}]
"""

filters_help = f"""Do some filter shit yo"""

@cli.command(help=request_help)
@click.argument("method", help="The method to call (ex: system.info)", required=True)
@click.option("--params", "-p", help="The params to pass to the method")
@click.option("--filter", "filters", nargs=3, multiple=True, metavar="FIELD OP VALUE", help=filters_help)
@common_options
@click.pass_context
def request(
    ctx: click.RichContext,
    method: str,
    params: str | None = None,
    filters: tuple[tuple[str, str, str], ...] = ()
) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    request_helper = get_request_helper()
    if not request_helper:
        log.error("TrueNAS API Conduit service is not running")
        sys.exit(1)

    # NOTE: The TrueNAS API needs params to be in TRIPLE NESTED LISTS.
    # It is indeed kind of fuckin unhinged and it took me a while to figure out because
    # they don't care much about whether their docs are easy to understand.

    # Outermost []: the JSON-RPC params array, where each element is a positional argument
    #               to the method
    #    Middle []: the filters argument itself, a list of conditions
    # Innermost []: a single condition, e.g. ["name", "=", "sda"]

    # down below when we combine the filters and params, we wrap it in an additional
    # list: combined = [filters_list + params_list]
    # This wll give us the final triple nested list we need

    filters_list = [list(f) for f in filters] if filters else []
    params_list: list[list[Any]] = []
    
    if params:
        log.debug("Raw params: %s", params)
        if params.find('“') != -1:
            raise click.UsageError(
                """You used the fancy smart quotes symbol (“) instead of the regular """
                """doublequotes (")."""
            )
        if not params.strip().startswith("["):
            raise click.UsageError(
                "First character must be an opening bracket: ["
            )
        if params.strip()[1] == "'" or params.strip()[2]  == "'":
            raise click.UsageError(
                """You must use double quotes inside the params list, and encase it """
                """with single quotes. (ex: '[["name", "=", "sda"]]')"""
            )
        try:
            params_list = json.loads(params)
        except json.JSONDecodeError as e:
            raise click.UsageError(f"Malformed params: {params}\n{e}")
        log.info("Method: %s | Params list: %s", method, params_list)
    
    combined = [filters_list + params_list]
    log.info("Full request params: %s", combined)
    response = request_helper(
        core.Endpoints.RPC, {"method": method, "params": combined}
    )
    ctx.console.print(response)


@cli.command()
@common_options
@click.pass_context
def stop(ctx: click.RichContext) -> None:
    """Stop the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None

    request_helper = get_request_helper()
    if not request_helper:
        log.error("TrueNAS API Conduit service is not running")
        sys.exit(1)

    response = request_helper(core.Endpoints.SHUTDOWN, {})  # needs empty dict to POST
    ctx.console.print(response)


@cli.command()
@common_options
@click.pass_context
def restart(ctx: click.RichContext) -> None:
    """Restart the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None

    request_helper = get_request_helper()
    if not request_helper:
        log.error("TrueNAS API Conduit service is not running")
        sys.exit(1)

    response = request_helper(core.Endpoints.RESTART, {})
    ctx.console.print(response)


@cli.command()
@common_options
@click.pass_context
def status(ctx: click.RichContext) -> None:
    """Check the status of the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None

    request_helper = get_request_helper()
    if not request_helper:
        log.error("TrueNAS API Conduit service is not running")
        sys.exit(1)

    response = request_helper(core.Endpoints.STATUS)
    ctx.console.print(response)


@cli.command()
@common_options
@click.pass_context
def set_key(ctx: click.RichContext) -> None:
    """Sets the API key using whatever compatible keyring/secrets manager is
    available on your system"""

    logging_setup(ctx)

    log.debug("Setting API key")
    import keyring

    # TODO: Implement set API key


config_help = f"""Attempts to open the config file in your editor, if
[{MENU_COLORS['envvar']}]$EDITOR[/{MENU_COLORS['envvar']}] is set"""


@cli.command(help=config_help)
@common_options
@click.pass_context
def config(ctx: click.RichContext) -> None:

    logging_setup(ctx)

    editor = os.environ.get("EDITOR")
    if not editor:
        raise click.UsageError("No editor set. Set the $EDITOR environment variable")
    os.execvp(editor, [editor, core.CONFIG_PATH])


@cli.command()
@common_options
@click.pass_context
def config_path(ctx: click.RichContext) -> None:
    """Prints the path to the config file"""

    logging_setup(ctx)
    assert ctx.console is not None

    ctx.console.print(core.CONFIG_PATH)  # stdout for piping
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
    This can be saved and passed in to the service's stdin to start it.
    Logging/debug is separated out to stderr. Warning: This will output
    your full API key in plain text"""

    logging_setup(ctx)
    assert ctx.console is not None

    cfg = config_setup(ctx.obj)
    json_dict = cfg.model_dump_json(indent=2)

    ctx.console.print(json_dict)
    if ctx.obj.verbose == 0:
        console_stderr.print(
            f"\n[italic]Tip: set verbosity/logging to debug to see provenance[/italic]",
            markup=True,
        )


@cli.command()
@common_options
@click.pass_context
def version(ctx: click.RichContext) -> None:
    """Prints the version of the TrueNAS API Conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None

    ctx.console.print(f"{APP_NAME} version {__version__}")
