# standard library
import signal
import sys
import logging
import os
import json
from typing import Any, Callable, assert_never

# third-party
import rich_click as click
from click_didyoumean import DYMMixin

# project
from truenas_api_conduit import (
    __version__,
    APP_NAME,
    SERVICENAME,
    COLORS,
)
import truenas_api_conduit.core as core
from truenas_api_conduit.console import console_stderr, console_stdout
from truenas_api_conduit.cli_helpers import (
    CLIOptions,
    logging_setup,
    config_setup,
    make_usage_error_panel,
    make_success_panel,
    prompt_for_config,
)
from truenas_api_conduit.request_helper import get_request_helper
from truenas_api_conduit.service import get_service_manager, ServiceError

log = logging.getLogger(__name__)

# Rich-click Config
click.rich_click.COMMANDS_BEFORE_OPTIONS = True
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.THEME = "cargo-modern"
# colorschemes: #~ [default, star, quartz, quartz2, cargo, forest, nord, dracula, solarized]
# theme types: #~ [box, slim, modern, robo, nu]
# nord, dracula, and solarized are "risky" according to the docs.


def handle_exit(*_):
    console_stderr.print("\nCancelling.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)

# === Click Option Callbacks ===

# NOTE: The click option callback pattern
# https://click.palletsprojects.com/en/stable/advanced/#callbacks

# Normally option callbacks are used for validation and similar tasks.
# I'm using them here to perform the "global options" decorator pattern/hack.
# It's a fairly well known workaround in the Click community for this problem.

# Normally Click is designed so that global options would need to be passed in
# to the main command, with subcommands coming *after* the options, like this:
#    $ truenas-api -vv request some.request -fmt

# This is awkward and not how most other CLI frameworks handle this.
# Instead we want it to be possible to chuck global options at the end of the
# command, like this:
#    $ truenas-api request some.request -fmt -vv

# In order to achieve this, we need to use these callbacks combined with a common
# option group decorator (below), which we can then re-use across subcommands.
# I researched all the possible ways to solve this problem, and this seems to be
# the most recommended one.

# GLOBAL OPTIONS

# NOTE: Because these are global options, they will actually run more than once
# on subcommands. This is unfortunately necessary for this pattern to work.
# The reason for this is we need to put the common options decorator on the main
# command of the entire program (cli() function) so that they also show up at
# the bottom of the main help menu. Otherwise they'll only show up in the help
# menus for individual subcommands, which is not good UX. This solves the problem,
# but it adds a new problem of these options being set twice.

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


def set_pretty_param(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    ctx.ensure_object(CLIOptions)
    if ctx.obj.pretty is None:
        ctx.obj.pretty = value
    return value


def request_options(f: Callable) -> Callable:
    f = click.option(
        "-fmt",
        "--pretty",
        is_flag=True,
        callback=set_pretty_param,
        default=False,
        expose_value=False,
        help=pretty_help,
    )(f)
    return f


verbose_help = f"""Sets the verbosity/logging level.
[{COLORS.option}]-v[/{COLORS.option}] for info,
[{COLORS.option}]-vv[/{COLORS.option}] for debug,
[{COLORS.option}]-vvv[/{COLORS.option}] for trace
[env:[{COLORS.envvar}] TRUENAS_LOG_LEVEL[default]=]"""

no_color_help = f"""Disables color output. You must set the environment variable
to disable color in the help menu [env:[{COLORS.envvar}] NO_COLOR[default]=]"""


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
    "logs",
]

config_commands = [
    "config",
    "set_key",
    "completions",
    "env",
]

help_commands = [
    "cheatsheet",
    "reference",
    "version",
    "help",
]

global_options = [
    "verbose",
    "no_color",
]


context = {
    "rich_console": console_stdout,
}

main_help = """TrueNAS API Conduit - A websocket proxy service for the TrueNAS API.\n
This will hold the websocket connection open so that subsequent requests can
re-use the same connection. It can be installed as a service, or run as a
standalone program without installing.\n
Most of the commands have more info in their respective help menus"""


@click.group(cls=CustomGroup, context_settings=context, help=main_help)
@click.command_panel("Commands", commands=main_commands)
@click.command_panel("Config", commands=config_commands)
@click.command_panel("Help", commands=help_commands)
@common_options
@click.pass_context
def cli(ctx: click.RichContext) -> None:

    ctx.ensure_object(CLIOptions)

    # NOTE: having the common_options decorator on the main group means those
    # options are visible in the main help menu which is important for UX. See
    # the big comment near the top of this file for more info.

    # The setup functions (logging_setup, config_setup) cannot be
    # run here, because they would not catch options that were passed into
    # the subcommands. If global options are set on the main command, they'll
    # be passed through in the click context, so that the subcommand gets
    # the full context when it does the setups.


start_help_short = """Tell your OS to start the conduit service"""

start_help = f"""Tell your OS to start the conduit service.\n
\n
You can also start the program directly as a standalone program without installing
by using the [{COLORS.option}]--standalone[/{COLORS.option}] option, which
runs in the foreground by default.\n
\n
Tip: to run standalone in the background, use:\n
\n
(Mac + Linux):  [{COLORS.command}]truenas-api start --standalone & disown[default]\n
(Windows):      [{COLORS.command}]Start-Process truenas-api start
--standalone[default]"""

standalone_help = """Start the service as a standalone program in the foreground (not
run by your service manager). Does not require installation"""

api_key_help = f"""(Only with [{COLORS.command}]--standalone[default])
Ask to be prompted for your TrueNAS API key. You can also use the
[{COLORS.command}]set-key[default] command (recommended), set the
[{COLORS.envvar}]api_key[default] field in the config file, or set the
environment variable [env: [{COLORS.envvar}]TRUENAS_API_KEY[default]=]"""

truenas_host_help = f"""(Only with [{COLORS.command}]--standalone[default])
The address that you use to access the TrueNAS Web UI over
HTTPS. You can also set the [{COLORS.envvar}]truenas_host[default]
field in the config file, or set the environment variable
[env:[{COLORS.envvar}] TRUENAS_HOST[default]=]"""


@cli.command(help=start_help, short_help=start_help_short)
@click.option("--standalone", is_flag=True, default=False, help=standalone_help)
@click.option("-api", "--api-key", is_flag=True, default=None, help=api_key_help)
@click.option("-host", "--truenas-host", help=truenas_host_help)
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

    if (api_key or truenas_host) and not standalone:
        raise click.UsageError(
            "You can only use the --api-key and --truenas-host options with --standalone"
        )

    ctx.obj.api_key = api_key
    ctx.obj.truenas_host = truenas_host
    cfg = config_setup(ctx.obj)

    if standalone:
        log.info("Starting service in foreground")

        cfg_dump = cfg.model_dump_json(context={"unmask": True})

        try:
            # file descriptors are just indices into the process's open file table,
            # the kernel tracks the actual file/pipe/socket, and hands you back a small
            # integer as a handle to refer to it. os.pipe gives you two of those indices.
            # Windows has its own handle system but python's os.pipe, os.dup2, and
            # os.write abstract over that

            # os.pipe creates two connected file descriptors, anything written to
            # write_fd can be read from read_fd.
            read_fd, write_fd = os.pipe()

            # os.write takes a file descriptor and data. We dump into the pipe buffer,
            # then close the write end. Since the pipe buffer is in kernel space and
            # the write end is now closed, the read end will see EOF once the data
            # is consumed (no hanging)
            os.write(write_fd, cfg_dump.encode())
            os.close(write_fd)

            # os.dup2 takes two file descriptors and copies the first to the second.
            # We're copying our read_fd onto fd 0 (stdin is fd 0), so that when the
            # program launches, it will see our read_fd as stdin.
            os.dup2(read_fd, 0)

            # os.close(read_fd) cleans up the now-redundant original read_fd, fd 0
            # already holds the pipe, so you don't need two references to it.
            os.close(read_fd)

            # * This shall henceforth be known as The execvp stdin Chad Swap

            # The new program inherits all open file descriptors, including fd 0,
            # which it now sees as normal stdin
            os.environ["TRUENAS_APP_ENV"] = core.AppEnv.STANDALONE.value
            os.execvp(SERVICENAME, [SERVICENAME])
        except OSError as e:
            err_string = core.examine_os_error(e)
            if cfg.log_level == "trace":
                raise
            else:
                log.error("Error restarting service: %s", err_string)

    else:
        log.info("Telling OS to start the service")
        from truenas_api_conduit.core import PLATFORM

        service = get_service_manager(PLATFORM)
        log.info("Service: %s", service)

        # service manager will check if its installed and exit if not
        try:
            service.start()
        except Exception as e:
            if cfg.log_level == "trace":
                raise
            else:
                action = "starting"
                if isinstance(e, ServiceError):
                    err_string = f"Encountered a systemd/systemctl error while {action} the service: "
                else:
                    err_string = f"Unexpected error while {action} the service: "
                err_string += f"\n\n{e} ({e.__class__.__name__})"
                panel = make_usage_error_panel(err_string, "Service Start Error")
                console_stderr.print(panel)
                sys.exit(1)
        else:
            ctx.console.print("TrueNAS API Conduit service started successfully")


install_help_short = """Install the TrueNAS API Conduit service"""

install_help = """Install the TrueNAS API Conduit service.\n
On Linux and MacOS, this will install as a user service and does not
require elevation. On Windows, elevation is required to install

On Linux: registers the program with systemd
On MacOS: registers the program with launchd
On Windows: registers the program with the Windows Service Manager
"""


@cli.command(help=install_help, short_help=install_help_short)
@common_options
@click.pass_context
def install(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    if not click.confirm(
        f"This will install the {APP_NAME} service. Continue?", default=True
    ):
        console_stderr.print("Cancelled")
        sys.exit(1)

    cfg = config_setup(ctx.obj)
    service = get_service_manager(core.PLATFORM)

    try:
        service.install()
    except Exception as e:
        if cfg.log_level == "trace":
            raise
        else:
            action = "installing"
            if isinstance(e, ServiceError):
                err_string = (
                    f"Encountered a systemd/systemctl error while {action} the service: "
                )
            else:
                err_string = f"Unexpected error while {action} the service: "
            err_string += f"\n\n{e} ({e.__class__.__name__})"
            panel = make_usage_error_panel(err_string, "Service Start Error")
            console_stderr.print(panel)
            sys.exit(1)
    else:
        ctx.console.print("TrueNAS API Conduit service installed successfully")


@cli.command()
@common_options
@click.pass_context
def uninstall(ctx: click.RichContext) -> None:
    """Uninstall the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    if not click.confirm(
        f"This will uninstall the {APP_NAME} service. Continue?", default=True
    ):
        console_stderr.print("Cancelled")
        sys.exit(1)

    cfg = config_setup(ctx.obj)
    service = get_service_manager(core.PLATFORM)

    try:
        service.uninstall()
    except Exception as e:
        if cfg.log_level == "trace":
            raise
        else:
            action = "uninstalling"
            if isinstance(e, ServiceError):
                err_string = (
                    f"Encountered a systemd/systemctl error while {action} the service: "
                )
            else:
                err_string = f"Unexpected error while {action} the service: "
            err_string += f"\n\n{e} ({e.__class__.__name__})"
            panel = make_usage_error_panel(err_string, "Service Start Error")
            console_stderr.print(panel)
            sys.exit(1)
    else:
        ctx.console.print("TrueNAS API Conduit service was uninstalled")


request_help_short = "Make a request using the service. The service must be running"


param_ex = """truenas-api request reporting.get_data --params '[{"name": "cpu"}]'"""

request_help = f"""Make a request using the service. The service must be running.\n
Example: [{COLORS.command}]truenas-api request system.info[default]\n
\n
You can also pipe the response into jq to filter and format the results:\n
[{COLORS.command}]truenas-api request disk.query | jq[default]\n
\n
Example of using the --params option (most TrueNAS API methods
can accept parameters to filter the results):\n
[{COLORS.command}]{param_ex}[default]\n
\n
The --filter option is a shortcut for passing in filter triplet arrays
to the --params option. Each -f flag (stackable) is equivalent to passing in a
single filter triplet. For example:\n
[{COLORS.command}]truenas-api request app.query -f name = 'dockge'[default]\n
\n
Use the [{COLORS.command}]cheatsheet[default] command to see a bigger list
of some common requests and usage examples.\n
Use the [{COLORS.command}]reference[default] command to print the URL to the API
reference on your server for the full list of everything you can request.\n
\n
Note: this program has no knowledge of what methods are available, it just
forwards the request to the TrueNAS API and returns the JSON response verbatim.
This will also return any TrueNAS errors to you if the request worked
but you've requested something invalid."""

filters_help = f"""Add a filter to the request. Filters are in the form of
'filter triplets' as defined by the TrueNAS API. Triplet format is
[{COLORS.envvar}]FIELD OPERATOR VALUE[default]. For example:
[{COLORS.option}]--filter name = sda[/{COLORS.option}]
"""

pretty_help = f"""Format the JSON response to be human-readable. Alternatively
you can pipe the response into
[{COLORS.command}]jq[default] (can be faster)"""


@cli.command(help=request_help, short_help=request_help_short)
@click.argument("method", help="The method to call (ex: system.info)", required=True)
@click.option("--params", "-p", help="The params to pass to the method")
@click.option(
    "-f",
    "--filter",
    "filters",
    nargs=3,
    multiple=True,
    metavar="FIELD OP VALUE",
    help=filters_help,
)
@request_options
@common_options
@click.pass_context
def request(
    ctx: click.RichContext,
    method: str,
    params: str | None = None,
    filters: tuple[tuple[str, str, str], ...] = (),
) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    request_helper = get_request_helper()
    log.debug(request_helper)
    if not request_helper:
        console_stderr.print(
            make_usage_error_panel("TrueNAS API Conduit service is not running")
        )
        sys.exit(1)

    # NOTE: The TrueNAS API needs params to be in TRIPLE NESTED LISTS.
    # It is indeed kind of unhinged and it took me a while to figure out
    # because their docs are not the easiest to understand.

    # Outermost []: the JSON-RPC params array, where each element is a positional argument
    #               to the method
    #    Middle []: the filters argument itself, a list of conditions
    # Innermost []: a single condition, e.g. ["name", "=", "sda"]

    # down below when we combine the filters and params, we wrap it in an additional
    # list: combined = [filters_list + params_list]
    # This wll give us the final triple nested list we need

    #     Supported Operators
    # | Operator | Description                           |
    # | -------- | ------------------------------------- |
    # |   =      | x == y                                |
    # |   !=     | x != y                                |
    # |   >      | x > y                                 |
    # |   >=     | x >= y                                |
    # |   <      | x < y                                 |
    # |   <=     | x <= y                                |
    # |   ~      | re.match(y, x)                        |
    # |   in     | x in y                                |
    # |   nin    | x not in y                            |
    # |   rin    | x is not None and y in x              |
    # |   rnin   | x is not None and y not in x          |
    # |   ^      | x is not None and x.startswith(y)     |
    # |   !^     | x is not None and not x.startswith(y) |
    # |   $      | x is not None and x.endswith(y)       |
    # |   !$     | x is not None and not x.endswith(y)   |

    supported_operators = (
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "~",
        "in",
        "nin",
        "rin",
        "rnin",
        "^",
        "!^",
        "$",
        "!$",
    )

    err_string = (
        """\nFilters must be in the format of FIELD OPERATOR VALUE\n"""
        "Example: --filter name = sda\n"
        "See TrueNAS API reference for more info (Tip: use "
        f"[{COLORS.command}]truenas-api reference[default] "
        "to print the URL to the API reference on your server)\n"
    )

    filters_list: list[list[str | int | bool | None]] = (
        [list(f) for f in filters] if filters else []
    )
    for f in filters_list:
        if len(f) != 3:
            console_stderr.print(make_usage_error_panel(err_string))
            sys.exit(1)
        if f[1] not in supported_operators:
            console_stderr.print(make_usage_error_panel(err_string))
            sys.exit(1)
        if f[2] in ("True", "true"):
            f[2] = True
        elif f[2] in ("False", "false"):
            f[2] = False
        elif f[2] in ("None", "none"):
            f[2] = None
        else:
            int_keys = ("id", "size", "allocated", "free", "number")
            if f[0] in int_keys:
                try:
                    f2_int = int(f[2])  # type: ignore
                except ValueError, TypeError:
                    console_stderr.print(
                        make_usage_error_panel(
                            (f"{f[0]} value must be an integer. Got: {f[2]}")
                        )
                    )
                    sys.exit(1)
                else:
                    f[2] = f2_int

    params_list: list[list[Any]] = []

    if params:
        log.debug("Raw params: %s", params)
        if "“" in params:
            console_stderr.print(
                make_usage_error_panel(
                    """You used the fancy smart quotes symbol (“) instead of the regular """
                    """doublequotes (")."""
                )
            )
            sys.exit(1)
        if not params.strip().startswith("["):
            console_stderr.print(
                make_usage_error_panel("First character must be an opening bracket: [")
            )
            sys.exit(1)
        if params.strip()[1] == "'" or params.strip()[2] == "'":
            console_stderr.print(
                make_usage_error_panel(
                    """You must use double quotes inside the params list, and encase it """
                    """with single quotes. (ex: '[["name", "=", "sda"]]')"""
                )
            )
            sys.exit(1)
        try:
            params_list = json.loads(params)
        except json.JSONDecodeError as e:
            console_stderr.print(
                make_usage_error_panel(f"Malformed params: {params}\n{e}")
            )
            sys.exit(1)
        log.info("Method: %s | Params list: %s", method, params_list)

    if filters_list or params_list:
        combined = [filters_list + params_list]
    else:
        combined = []
    log.info("Full request params: %s", combined)

    response = request_helper(
        core.Endpoints.REQUEST, {"method": method, "params": combined}
    )
    if ctx.obj.pretty:
        try:
            ctx.console.print(json.dumps(response.json(), indent=2), soft_wrap=True)
        except json.JSONDecodeError as e:
            log.error(
                "Response from server is not valid JSON: %s | Disable pretty "
                "printing to see the raw response",
                e,
            )
            if ctx.obj.verbose >= 3:
                raise
            else:
                sys.exit(1)
    else:
        ctx.console.print(response.text, soft_wrap=True)


stop_help = """Stop the conduit service. This will detect if its running
as an OS service or in standalone mode and send the stop request accordingly"""

stop_direct_help = """Force the stop request to go directly to the service,
bypassing the OS service manager (only relevant if installed, standalone
mode does this automatically)"""


@cli.command(help=stop_help)
@click.option("-d", "--direct", is_flag=True, default=False, help=stop_direct_help)
@common_options
@click.pass_context
def stop(ctx: click.RichContext, direct: bool = False) -> None:

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    # TWO WAYS TO STOP
    # 1) Send the service a stop request directly
    # 2) Tell service manager to stop the service

    service = get_service_manager(core.PLATFORM)

    # This will tell us if the service is installed or not
    detect = service.detect_service()
    log.info("Detected service running as: %s", detect)

    if (detect == core.AppEnv.STANDALONE) or direct:
        # Option 1: Sending a request
        request_helper = get_request_helper()
        log.debug(request_helper)
        if not request_helper:
            console_stderr.print(
                make_usage_error_panel("TrueNAS API Conduit service is not running")
            )
            sys.exit(1)

        response = request_helper(core.Endpoints.STOP, {})  # needs empty dict to POST
        if ctx.obj.pretty:
            try:
                ctx.console.print(json.dumps(response.json(), indent=2), soft_wrap=True)
            except json.JSONDecodeError as e:
                log.error(
                    "Response from server is not valid JSON: %s | Disable pretty "
                    "printing to see the raw response",
                    e,
                )
                if ctx.obj.verbose >= 3:
                    raise
                else:
                    sys.exit(1)
        else:
            ctx.console.print(response.text, soft_wrap=True)

    elif detect == core.AppEnv.OS_SERVICE:
        try:
            service.stop()
        except Exception as e:
            # We don't load config for this command so just check log level
            log_level = logging.getLogger().level
            level_name = logging.getLevelName(log_level)
            if level_name.lower() == "trace":
                raise
            else:
                action = "stopping"
                if isinstance(e, ServiceError):
                    err_string = f"Encountered a systemd/systemctl error while {action} the service: "
                else:
                    err_string = f"Unexpected error while {action} the service: "
                err_string += f"\n\n{e} ({e.__class__.__name__})"
                panel = make_usage_error_panel(err_string, "Service Start Error")
                console_stderr.print(panel)
                sys.exit(1)
        else:
            ctx.console.print("TrueNAS API Conduit service was stopped")

    elif detect == core.AppEnv.DOCKER:
        err_panel = make_usage_error_panel(
            "You cannot stop the service in Docker mode. Stop the docker container instead."
        )
        console_stderr.print(err_panel)
        sys.exit(1)
    else:
        assert_never(detect)


restart_help = """Restart the conduit service. This will detect if its running
as an OS service or in standalone mode and send the restart request accordingly"""

restart_direct_help = """Force the restart request to go directly to the service,
bypassing the OS service manager (only relevant if installed, standalone
mode does this automatically)"""


@cli.command(help=restart_help)
@click.option("-d", "--direct", is_flag=True, default=False, help=restart_direct_help)
@common_options
@click.pass_context
def restart(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    # TWO WAYS TO STOP
    # 1) Send the service a stop request
    # 2) Tell service manager to stop the service

    service = get_service_manager(core.PLATFORM)
    detect = service.detect_service()
    log.info("Detected service running as: %s", detect)

    if detect == core.AppEnv.STANDALONE:
        request_helper = get_request_helper()
        log.debug(request_helper)
        if not request_helper:
            console_stderr.print(
                make_usage_error_panel("TrueNAS API Conduit service is not running")
            )
            sys.exit(1)
        response = request_helper(core.Endpoints.RESTART, {})

        if ctx.obj.pretty:
            try:
                ctx.console.print(json.dumps(response.json(), indent=2), soft_wrap=True)
            except json.JSONDecodeError as e:
                log.error(
                    "Response from server is not valid JSON: %s | Disable pretty "
                    "printing to see the raw response",
                    e,
                )
                if ctx.obj.verbose >= 3:
                    raise
                else:
                    sys.exit(1)
        else:
            ctx.console.print(response.text, soft_wrap=True)

    elif detect == core.AppEnv.OS_SERVICE:
        try:
            service.restart()
        except Exception as e:
            # We don't load config for this command so just check log level
            log_level = logging.getLogger().level
            level_name = logging.getLevelName(log_level)
            if level_name.lower() == "trace":
                raise
            else:
                action = "restarting"
                if isinstance(e, ServiceError):
                    err_string = f"Encountered a systemd/systemctl error while {action} the service: "
                else:
                    err_string = f"Unexpected error while {action} the service: "
                err_string += f"\n\n{e} ({e.__class__.__name__})"
                panel = make_usage_error_panel(err_string, "Service Start Error")
                console_stderr.print(panel)
                sys.exit(1)
        else:
            ctx.console.print("TrueNAS API Conduit service restarted")

    elif detect == core.AppEnv.DOCKER:
        err_panel = make_usage_error_panel(
            "You cannot restart the service in Docker mode. Restart the docker container instead."
        )
        console_stderr.print(err_panel)
        sys.exit(1)
    else:
        assert_never(detect)


status_help_short = """Check the status/ping of the conduit service"""

status_help = """Check the status/ping of the conduit service.
This can query the service directly, or ask your operating system (if installed).\n
This returns the response in JSON (if not using the --system option)."""

system_status_help = "View the OS service manager's status output, if installed"


@cli.command(help=status_help, short_help=status_help_short)
@click.option("-sys", "--system", is_flag=True, default=False, help=system_status_help)
@request_options
@common_options
@click.pass_context
def status(ctx: click.RichContext, system: bool = False) -> None:

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    # TWO WAYS TO GET THE STATUS
    # 1) Send the service a status request
    # 2) Ask the service manager

    running = False
    if not system:
        # 1: Sending a request
        request_helper = get_request_helper()
        log.debug(request_helper)
        if request_helper:
            response = request_helper(core.Endpoints.STATUS)
            if ctx.obj.pretty:
                try:
                    ctx.console.print(
                        json.dumps(response.json(), indent=2), soft_wrap=True
                    )
                except json.JSONDecodeError as e:
                    log.error(
                        "Response from server is not valid JSON: %s | Disable pretty "
                        "printing to see the raw response",
                        e,
                    )
                    if ctx.obj.verbose >= 3:
                        raise
                    else:
                        sys.exit(1)
            else:
                ctx.console.print(response.text, soft_wrap=True)
                running = True
        else:  # no request helper
            ctx.console.print("TrueNAS API Conduit service is not running")
            # NOTE: here we don't immediately exit, proceed to check if the
            # service is installed

    if system or not running:
        # 2: Asking the service manager
        service = get_service_manager(core.PLATFORM)
        detect = service.detect_service()
        log.info("Detected service running as: %s", detect)

        if detect == core.AppEnv.STANDALONE:
            # Not installed, just exit
            sys.exit(1)

        if system and (detect != core.AppEnv.OS_SERVICE):
            console_stderr.print(
                make_usage_error_panel(
                    "--system can only be used with the service in OS mode"
                )
            )
            sys.exit(1)

        try:
            service.status(forward_stdout=system)
        except Exception as e:
            # We don't load config for this command so just check log level
            log_level = logging.getLogger().level
            level_name = logging.getLevelName(log_level)
            if level_name.lower() == "trace":
                raise
            else:
                action = "checking status of"
                if isinstance(e, ServiceError):
                    err_string = f"Encountered a systemd/systemctl error while {action} the service: "
                else:
                    err_string = f"Unexpected error while {action} the service: "
                err_string += f"\n\n{e} ({e.__class__.__name__})"
                panel = make_usage_error_panel(err_string, "Service Start Error")
                console_stderr.print(panel)


logs_helps_short = """Read the system logs for the service (must be installed)"""

# HACK: These help menus might be OS specific. I'll probably need to adjust
# the wording to make it applicable to Mac and Windows.

logs_help = f"""Read the system logs for the service (must be installed).\n
You can pipe this into a log viewer (such as 'lnav' or 'moor') to view the logs
in real time (ie.: [{COLORS.command}]truenas-api logs -f | lnav[default]).\n
Note that -f opens the system logger directly and will not have any color or
search capabilities. Recommended to install a proper log viewer TUI such as
`lnav` or `moor`"""

follow_help = """Follow/tail the log output (Note: This just runs the system logger
directly, which is why it can be piped, but it has no color by itself)"""

limit_help = "The number of logs to print. Exclusive with --follow"


@cli.command(help=logs_help, short_help=logs_helps_short)
@click.option("-f", "--follow", is_flag=True, default=False, help=follow_help)
@click.option("-l", "--limit", type=int, default=100, help=limit_help, show_default=True)
@common_options
@click.pass_context
def logs(ctx: click.RichContext, limit: int, follow: bool = False) -> None:

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    service = get_service_manager(core.PLATFORM)
    detect = service.detect_service()
    log.info("Detected service running as: %s", detect)

    if detect != core.AppEnv.OS_SERVICE:
        console_stderr.print(
            make_usage_error_panel("You can only get the logs for the service in OS mode")
        )
        sys.exit(1)

    try:
        logs = service.logs(follow=follow, limit=limit)
    except Exception as e:
        # We don't load config for this command so just check log level
        log_level = logging.getLogger().level
        level_name = logging.getLevelName(log_level)
        if level_name.lower() == "trace":
            raise
        else:
            action = "checking status of"
            if isinstance(e, ServiceError):
                err_string = (
                    f"Encountered a systemd/systemctl error while {action} the service: "
                )
            else:
                err_string = f"Unexpected error while {action} the service: "
            err_string += f"\n\n{e} ({e.__class__.__name__})"
            panel = make_usage_error_panel(err_string, "Service Start Error")
            console_stderr.print(panel)
    else:
        if logs and not follow:
            ctx.console.print(logs)
        else:
            ctx.console.print("No logs found")


set_key_help_short = """Set the API key using whatever compatible keyring/secrets manager
is available on your system"""

set_key_help = f"""Set the API key using whatever compatible keyring/secrets manager
is available on your system.\n
If there is no keyring backend available (ie. you're running in some minimal or
headless environment), the program will fall back to writing the API key to an
encrypted file in your storage directory. If this happens, the program will
look for the [{COLORS.envvar}]{core.CRYPT_KEY_ENV}[default] environment variable.
If available, it will use that as the encryption key to avoid prompting you (thus
making it possible to start the service through scripts/non-interactive environments).\n
If this env var is NOT set, the program will prompt you for the encryption key
when you run the
[{COLORS.command}]set-key[default] command, as well as every time the service
starts up. This would be unsuitable for starting at boot or other such automations
[env: [{COLORS.envvar}]{core.CRYPT_KEY_ENV}[default]=]
"""

delete_help = "Delete the API key from the current keyring backend."
show_help = """Show the API key in the current keyring backend
(You can pipe this into a file to save it)."""
del_crypt_help = "Delete the stored encryption key file, if it exists."


@cli.command(help=set_key_help, short_help=set_key_help_short)
@click.option("-d", "--delete", is_flag=True, default=False, help=delete_help)
@click.option("-dc", "--del-crypt", is_flag=True, default=False, help=del_crypt_help)
@click.option("-s", "--show", is_flag=True, default=False, help=show_help)
@common_options
@click.pass_context
def set_key(
    ctx: click.RichContext,
    delete: bool = False,
    del_crypt: bool = False,
    show: bool = False,
) -> None:

    if delete and show:
        raise click.UsageError("You cannot specify both --delete and --show")
    if del_crypt and show:
        raise click.UsageError("You cannot specify both --del-crypt and --show")

    logging_setup(ctx)
    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None

    prompt_for_config()
    core.ensure_storage_dir()

    import keyring
    import keyring.errors as kr_errs
    import keyring.backend
    from truenas_api_conduit.config.crypt_key import store_crypt_key
    from truenas_api_conduit.config.file_encrypter import (
        FileEncrypter,
        PasswordGetError,
        GetErrorEnum,
    )

    # my custom fallback file encrypter keyring backend. This is set to
    # lowest priority (0.0) so that it should only be used if no other
    # keyring backends are available.
    file_encrypter = FileEncrypter()
    keyring.set_keyring(file_encrypter)

    # all_keyrings will always contain 'fail Keyring' and 'chainer ChainerBackend'
    # even if no other keyrings are present.
    all_keyrings = keyring.backend.get_all_keyring()
    log.debug(f"Available keyring backends: {[k.name for k in all_keyrings]}")

    current_backend = keyring.get_keyring()
    log.debug(f"Current keyring backend: {current_backend.name}")

    service = APP_NAME
    username = "api_key"

    action_desc = "<action>"
    actions: list[str] = []
    try:
        if delete or del_crypt:
            if delete:
                log.info("Deleting API key from '%s'", current_backend.name)
                action_desc = "delete API key"
                keyring.delete_password(service, username)
                log.debug("Deleted API key from keyring")
                actions.append(action_desc)
            if del_crypt:
                action_desc = "delete crypt key file"
                if not core.CRYPT_KEY_PATH.exists():
                    if delete:
                        log.error(f"No crypt key file found ({core.CRYPT_KEY_PATH})")
                    else:
                        ctx.console.print(
                            make_usage_error_panel(
                                "No crypt key file found", "Keyring Error"
                            )
                        )
                else:
                    log.info("Deleting crypt key file")
                    action_desc = "delete crypt key file"
                    core.CRYPT_KEY_PATH.unlink()
                    log.debug(f"Deleted crypt key file ({core.CRYPT_KEY_PATH})")
                    actions.append(action_desc)
        elif show:
            log.info("Showing API key from '%s'", current_backend.name)
            action_desc = "show API key"
            api_key = keyring.get_password(service, username)
            if api_key:
                ctx.console.print(api_key)
                actions.append(action_desc)
            else:
                ctx.console.print(
                    make_usage_error_panel("No API key found in keyring", "Keyring Error")
                )
        else:
            log.info("Setting API key in '%s'", current_backend.name)
            log.warning("This will overwrite any existing key you have set")
            action_desc = "set API key"
            api_key = click.prompt("Enter your TrueNAS API key", hide_input=True)
            keyring.set_password(service, username, api_key)
            log.debug("Success: key set")
            actions.append(action_desc)
            if "FileEncrypter" in current_backend.name:
                action_desc = "store crypt key in file"
                if store_crypt_key(file_encrypter.crypt_key):
                    actions.append(action_desc)
    except click.Abort:
        raise
    except PasswordGetError as e:
        # This is my custom error class so it will only happen if keyring tried
        # to use my fallback FileEncrypter backend, and the user password was
        # not found.
        err_string: str | None = None
        if e.err_code == GetErrorEnum.INCORRECT_ENCRYPTION_KEY:
            err_string = "The encryption key you have entered is incorrect."
            console_stderr.print(make_usage_error_panel(err_string, "Keyring Error"))
            sys.exit(1)
        else:
            if ctx.obj.verbose >= 3:
                raise
            else:
                log.error(
                    "Unexpected error: %s | Raise the verbosity to see more information"
                )
                sys.exit(1)
    except (
        kr_errs.KeyringError,
        kr_errs.PasswordSetError,
        kr_errs.PasswordDeleteError,
    ) as e:
        log.error("Keyring error: %s", e)
        if ctx.obj.verbose >= 3:
            raise
        else:
            err_string = str(e)
            console_stderr.print(make_usage_error_panel(err_string, "Keyring Error"))
            sys.exit(1)
    except FileNotFoundError as e:
        err_string = str(e)
        console_stderr.print(make_usage_error_panel(err_string, "File Error"))
        sys.exit(1)
    except Exception as e:
        log.error(
            "Failed keyring action: %s (%s) | %s", action_desc, e.__class__.__name__, e
        )
        if ctx.obj.verbose >= 3:
            raise
        else:
            sys.exit(1)
    else:
        if actions:
            success_string = ""
            for i, action in enumerate(actions):
                if action == "show API key":
                    return
                success_string += f"Success: {action}"
                if i < len(actions) - 1:
                    success_string += "\n"
            ctx.console.print(make_success_panel(success_string))


config_help = f"""Attempts to open the config file in your editor, if
[env: [{COLORS.envvar}]EDITOR[default]=] is set. Contains options to print
the path to the config file, or print the config to stdout, etc."""

print_path_help = "Print the path to the config file (you can pipe this)"

print_config_help = f"""Validate and output your current configuration as JSON to
stdout. If you combine the [{COLORS.command}]--unmask[default] option, then the
generated JSON may be piped to truenas-api-conduitd via stdin.
"""

unmask_help = f"""(Used with [{COLORS.command}]--print-config[default]) reveals the
API key in the JSON output. May trigger a password prompt"""


@cli.command(help=config_help)
@click.option("-p", "--print-path", is_flag=True, default=False, help=print_path_help)
@click.option("-c", "--print-config", is_flag=True, default=False, help=print_config_help)
@click.option("-u", "--unmask", is_flag=True, default=False, help=unmask_help)
@common_options
@click.pass_context
def config(
    ctx: click.RichContext,
    print_path: bool = False,
    print_config: bool = False,
    unmask: bool = False,
) -> None:

    if print_path and print_config:
        raise click.UsageError("You cannot specify both --print-path and --print-config")

    if unmask and not print_config:
        raise click.UsageError("--unmask must be used with --print-config")

    logging_setup(ctx)
    assert ctx.console is not None
    ctx.console.no_color = True

    prompt_for_config()

    if print_path:
        ctx.console.no_color = True
        ctx.console.print(core.CONFIG_PATH)  # stdout for piping
        console_stderr.print(f"Created already?: {core.CONFIG_PATH.exists()}")
        console_stderr.print(
            f"[italic]Tip: You can pipe this command into an editor:[/italic]"
            f"""  [{COLORS.command}]nano $(truenas-api config -p)""",
            markup=True,
        )

    elif print_config:
        cfg = config_setup(ctx.obj, unmask=unmask)
        json_dict = cfg.model_dump_json(indent=2, context={"unmask": unmask})

        ctx.console.print(json_dict)
        if ctx.obj.verbose == 0:
            console_stderr.print(
                "\n[italic]Tip: set verbosity/logging to info to see provenance[/italic]",
                markup=True,
            )
    else:
        editor = os.environ.get("EDITOR")
        if editor:
            console_stderr.print(
                "Remember you must restart the service to apply any changes",
                style="italic",
            )
            os.execvp(editor, [editor, core.CONFIG_PATH])
        else:
            err_string = (
                f"No editor set. Set the [{COLORS.envvar}]$EDITOR[default] "
                "environment variable"
            )
            console_stderr.print(make_usage_error_panel(err_string), "Config Error")
            sys.exit(1)


cheatsheet_help = (
    """Print a cheatsheet showing how to do a bunch of commmon API requests"""
)


@cli.command(help=cheatsheet_help)
@common_options
@click.pass_context
def cheatsheet(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    from truenas_api_conduit.cheatsheet import get_tables

    ctx.console.print()
    for table in get_tables():
        ctx.console.print(table)
    ctx.console.print(
        "\n  Remember that you can always pipe the response into jq to filter "
        "and format the results\n\n"
        "  Read the TrueNAS API reference for a full list of all available "
        "methods and their parameters\n",
        f" Tip: use the [{COLORS.command}]reference[default] command "
        "to print the URL to the API reference on your server\n",
        style="italic",
    )


reference_help = """Print the URL to the TrueNAS API reference on your server
(requires your config to be set up)"""


@cli.command(help=reference_help)
@common_options
@click.pass_context
def reference(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    if not core.CONFIG_DIR.exists():
        console_stderr.print(
            make_usage_error_panel(
                "The config directory has not been created yet", "Config Error"
            )
        )
        sys.exit(1)

    if not core.CONFIG_PATH.exists():
        console_stderr.print(
            make_usage_error_panel("Config file not found", "Config Error")
        )
        sys.exit(1)

    cfg = config_setup(ctx.obj)

    ctx.console.print(f"https://{cfg.truenas_host}/api/docs/current")


version_help = """Print the version of the TrueNAS API Conduit service"""


@cli.command(help=version_help)
@common_options
@click.pass_context
def version(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    # TODO: This should have some method of pinning which version of the TrueNAS
    # API its written for.

    ctx.console.print(f"{APP_NAME} {__version__}")


completions_help = """Print the commands to enable tab completions in your shell
(you can eval this)"""


@cli.command(help=completions_help)
@click.argument(
    "shell",
    required=False,
    type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False),
)
@common_options
@click.pass_context
def completions(ctx: click.RichContext, shell: str | None) -> None:
    assert ctx.console is not None
    ctx.console.no_color = True

    # Track if the user explicitly provided the shell argument
    user_provided_shell = shell is not None

    # 1. Resolve target shell (Argument > Auto-detect > Fallback)
    if not shell:
        if shell_env := os.environ.get("SHELL"):
            shell = shell_env.split("/")[-1].lower()
        else:
            shell = "bash"

    # Ensure unsupported auto-detected shells gracefully fallback to bash
    if shell not in ("bash", "zsh", "fish"):
        shell = "bash"

    # 2. Base Configuration (Defaults to Bash)
    command = "bash_source"
    eval_template1 = 'eval "$(_TRUENAS_API_COMPLETE={command} truenas-api)"'
    eval_template2 = (
        'eval "$(_TRUENAS_API_CONDUIT_COMPLETE={command} truenas-api-conduit)"'
    )

    # 3. Adjust for specific shells
    if shell == "zsh":
        command = "zsh_source"
    elif shell == "fish":
        command = "fish_source"
        eval_template1 = "_TRUENAS_API_COMPLETE={command} truenas-api | source"
        eval_template2 = (
            "_TRUENAS_API_CONDUIT_COMPLETE={command} truenas-api-conduit | source"
        )

    # Print the raw completion script triggers
    ctx.console.print(eval_template1.format(command=command))
    ctx.console.print(eval_template2.format(command=command))

    # 4. Generate dynamic tip based on the resolved shell
    # If the user passed the shell manually, include it in the tip's instruction
    shell_suffix = f" {shell}" if user_provided_shell else ""

    if shell == "fish":
        eval_instruction = f"truenas-api completions{shell_suffix} | source"
    else:
        eval_instruction = f"eval $(truenas-api completions{shell_suffix})"

    console_stderr.print(
        "\n[italic]Note: the truenas-api command is the same thing as "
        "the truenas-api-conduit command, just shorter.\n\n"
        "You can run this command to enable tab completions "
        f"(detected shell: {shell}):[/italic]\n"
        f"[{COLORS.command}]{eval_instruction}[default]",
        markup=True,
    )


env_help = """Print out a list of all environment variables which can be used by
the service, and their current values"""


@cli.command(help=env_help)
@common_options
@click.pass_context
def env(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    for k in core.ENV_VARS:
        ctx.console.print(f"{k}: {os.environ.get(k)}")
