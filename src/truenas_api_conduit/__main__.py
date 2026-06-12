# standard library
import signal
import sys
import logging
import os
import json
from typing import Any, Callable

# third-party
import rich_click as click
from click_didyoumean import DYMMixin

# project
from truenas_api_conduit import (
    __version__,
    APP_NAME,
    SERVICENAME,
    COLORS,
    Endpoints,
    InstallType,
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
]

config_commands = [
    "set_key",
    "config",
    "config_path",
    "print_config",
    "completions",
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


@click.group(cls=CustomGroup, context_settings=context)
@click.command_panel("Commands", commands=main_commands)
@click.command_panel("Config", commands=config_commands)
@click.command_panel("Help", commands=help_commands)
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


start_help_short = f"""Tell your OS to start the conduit service
([{COLORS.command}]start --help[default] for more info)"""

start_help = f"""Tell your OS to start the conduit service.\n
You can also start the program directly as a standalone program without installing
by using the [{COLORS.option}]--standalone[/{COLORS.option}] option, which
runs in the foreground by default.\n
Tip: to run standalone in the background, use:
[{COLORS.command}]truenas-api start --standalone & disown[default]
(Mac + Linux) or
[{COLORS.command}]Start-Process truenas-api start
--standalone[default] (Windows)"""

standalone_help = """Start the service as a standalone program in the foreground (not
run by your service manager). Does not require installation"""

api_key_help = f"""Ask to be prompted for your TrueNAS API key. You can also use the
[{COLORS.command}]set-key[default] command (recommended), set the
[{COLORS.envvar}]api_key[default] field in the config file, or set the
environment variable [env: [{COLORS.envvar}]TRUENAS_API_KEY[default]=]"""

truenas_host_help = f"""The address that you use to access the TrueNAS Web UI over
HTTPS. You can also set the [{COLORS.envvar}]truenas_host[default]
field in the config file, or set the environment variable
[env:[{COLORS.envvar}] TRUENAS_HOST[default]=]"""


@cli.command(help=start_help, short_help=start_help_short)
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
            os.execvp(SERVICENAME, [SERVICENAME])
        except OSError as e:
            err_string = core.examine_os_error(e)
            if cfg.log_level == "trace":
                raise
            elif cfg.log_level == "debug":
                log.exception("Error restarting service: %s", err_string)
            else:
                log.error("Error restarting service: %s", err_string)

    else:
        log.info("Telling OS to start the service")
        from truenas_api_conduit.core import PLATFORM

        service = get_service_manager(PLATFORM)
        log.info("Service: %s", service)
        
        # service manager will check if its installed and exit if not
        try:
            service.start(cfg)
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

install_help_short = f"""Install the TrueNAS API Conduit service
([{COLORS.command}]install --help[default] for more info)"""

install_help = """Install the TrueNAS API Conduit service.\n
On Linux and MacOS, the default is to install as a user service and does not
require elevation. On Windows, elevation is required to install"""

system_help = """Install the service as a system service. This requires elevation"""
package_help = """This is intended to be used by package managers"""


@cli.command(help=install_help, short_help=install_help_short)
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

    if system and package:
        raise click.UsageError("You cannot specify both --system and --package")

    if package:
        # TODO: need some validation to ensure this is actually being done by
        # the package manager. Might not even use this function, not sure yet.
        pass

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
        if system:
            service.install(InstallType.SYSTEM)
        elif package:
            service.install(InstallType.PACKAGE)
        else:
            service.install(InstallType.USER)
    except Exception as e:
        if cfg.log_level == "trace":
            raise
        else:
            action = "installing"
            if isinstance(e, ServiceError):
                err_string = f"Encountered a systemd/systemctl error while {action} the service: "
            else:
                err_string = f"Unexpected error while {action} the service: "
            err_string += f"\n\n{e} ({e.__class__.__name__})"
            panel = make_usage_error_panel(err_string, "Service Start Error")
            console_stderr.print(panel) 
            sys.exit(1)


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
                err_string = f"Encountered a systemd/systemctl error while {action} the service: "
            else:
                err_string = f"Unexpected error while {action} the service: "
            err_string += f"\n\n{e} ({e.__class__.__name__})"
            panel = make_usage_error_panel(err_string, "Service Start Error")
            console_stderr.print(panel) 
            sys.exit(1)


request_help_short = f"""Make a request using the service. The service must be running
([{COLORS.command}]request --help[default] for more info)"""

request_help = f"""Make a request using the service. The service must be running.\n
Example: [{COLORS.command}]truenas-api request system.info[default]\n
Use the [{COLORS.command}]cheatsheet[default] command to see a list
of some common requests and examples of how to use them"""

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

    response = request_helper(Endpoints.REQUEST, {"method": method, "params": combined})
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


@cli.command()
@request_options
@common_options
@click.pass_context
def stop(ctx: click.RichContext) -> None:
    """Stop the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    # TWO WAYS TO STOP
    # 1) Tell service manager to stop the service
    # 2) Send the service a stop request

    # Option 1: Service manager
    service = get_service_manager(core.PLATFORM)

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

    # Option 2: Sending a request
    request_helper = get_request_helper()
    log.debug(request_helper)
    if not request_helper:
        console_stderr.print(
            make_usage_error_panel("TrueNAS API Conduit service is not running")
        )
        sys.exit(1)

    response = request_helper(Endpoints.STOP, {})  # needs empty dict to POST
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


@cli.command()
@request_options
@common_options
@click.pass_context
def restart(ctx: click.RichContext) -> None:
    """Restart the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    # TWO WAYS TO STOP
    # 1) Tell service manager to stop the service
    # 2) Send the service a stop request

    # Option 1: Service manager
    from truenas_api_conduit.service import get_service_manager
    service = get_service_manager(core.PLATFORM)

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

    # Option 2: Sending a request
    request_helper = get_request_helper()
    log.debug(request_helper)
    if not request_helper:
        console_stderr.print(
            make_usage_error_panel("TrueNAS API Conduit service is not running")
        )
        sys.exit(1)

    response = request_helper(Endpoints.RESTART, {})
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


@cli.command()
@request_options
@common_options
@click.pass_context
def status(ctx: click.RichContext) -> None:
    """Check the status/ping of the conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None
    assert isinstance(ctx.obj, CLIOptions)

    # TWO WAYS TO GET THE STATUS
    # 1) Ask the service manager
    # 2) Send the service a status request

    # Option 1: Service manager
    from truenas_api_conduit.service import get_service_manager
    service = get_service_manager(core.PLATFORM)

    try:
        service.status(stdout=True)
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
            sys.exit(1)

    # Option 2: Sending a request
    request_helper = get_request_helper()
    log.debug(request_helper)
    if not request_helper:
        console_stderr.print(
            make_usage_error_panel("TrueNAS API Conduit service is not running")
        )
        sys.exit(1)

    response = request_helper(Endpoints.STATUS)
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


set_key_help_short = f"""Set the API key using whatever compatible keyring/secrets manager
is available on your system
([{COLORS.command}]set-key --help[default] for more info)"""

set_key_help = f"""Set the API key using whatever compatible keyring/secrets manager
is available on your system.\n
If there is no keyring backend available (ie. you're running in some minimal or
headless environment), the program will fall back to writing the API key to an
encrypted file in your storage directory. If this happens, the program will
look for the [{COLORS.envvar}]TRUENAS_CRYPT_KEY[default] environment variable.
If available, it will use that as the encryption key to avoid prompting you (thus
making it possible to start the service through scripts/non-interactive environments).\n
If this env var is NOT set, the program will prompt you for the encryption key
when you run the
[{COLORS.command}]set-key[default] command, as well as every time the service
starts up. This would be unsuitable for starting at boot or other such automations
[env: [{COLORS.envvar}]TRUENAS_CRYPT_KEY[default]=]
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
    keyring.set_keyring(FileEncrypter())

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
                if store_crypt_key(api_key):
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
[env: [{COLORS.envvar}]EDITOR[default]=] is set"""


@cli.command(help=config_help)
@common_options
@click.pass_context
def config(ctx: click.RichContext) -> None:

    logging_setup(ctx)
    assert ctx.console is not None
    ctx.console.no_color = True

    prompt_for_config()

    editor = os.environ.get("EDITOR")
    if editor:
        console_stderr.print(
            "Remember you must restart the service to apply any changes", style="italic"
        )
        os.execvp(editor, [editor, core.CONFIG_PATH])
    else:
        err_string = (
            f"No editor set. Set the [{COLORS.envvar}]$EDITOR[default] "
            "environment variable"
        )
        console_stderr.print(make_usage_error_panel(err_string), "Config Error")
        sys.exit(1)


@cli.command()
@common_options
@click.pass_context
def config_path(ctx: click.RichContext) -> None:
    """Print the path to the config file (you can pipe this)"""

    logging_setup(ctx)
    assert ctx.console is not None

    prompt_for_config()

    ctx.console.no_color = True
    ctx.console.print(core.CONFIG_PATH)  # stdout for piping
    console_stderr.print(f"Created already?: {core.CONFIG_PATH.exists()}")
    console_stderr.print(
        f"[italic]Tip: You can pipe this command into an editor:[/italic]"
        f"""  [{COLORS.command}]nano $(truenas-api config-path)""",
        markup=True,
    )


print_config_help_short = f"""Validate and output your current configuration as
JSON to stdout
([{COLORS.command}]print-config --help[default] for more info)"""

print_config_help = f"""Output your current configuration as JSON to
stdout. If you use the [{COLORS.command}]--unmask[default] option, then this
can be saved and passed in to the service's stdin to start it. If using --unmask
and the API key is stored with the [{COLORS.option}]set-key[default]
command, you may be prompted for a password for encryption key"""

unmask_help = """Output your API key in plain text. This is useful if you want to
store the configuration JSON in a file and pass it to the service's stdin to start
it. May trigger a password prompt"""


@cli.command(help=print_config_help, short_help=print_config_help_short)
@click.option("-u", "--unmask", is_flag=True, default=False, help=unmask_help)
@common_options
@click.pass_context
def print_config(ctx: click.RichContext, unmask: bool = False) -> None:

    logging_setup(ctx)
    assert ctx.console is not None

    prompt_for_config()

    cfg = config_setup(ctx.obj, unmask=unmask)
    json_dict = cfg.model_dump_json(indent=2, context={"unmask": unmask})

    ctx.console.print(json_dict)
    if ctx.obj.verbose == 0:
        console_stderr.print(
            "\n[italic]Tip: set verbosity/logging to info to see provenance[/italic]",
            markup=True,
        )


@cli.command()
@common_options
@click.pass_context
def cheatsheet(ctx: click.RichContext) -> None:
    """Print a cheatsheet showing how to do a bunch of commmon API requests"""

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


@cli.command()
@common_options
@click.pass_context
def version(ctx: click.RichContext) -> None:
    """Print the version of the TrueNAS API Conduit service"""

    logging_setup(ctx)
    assert ctx.console is not None

    # TODO: This should have some method of pinning which version of the TrueNAS
    # API its written for.

    ctx.console.print(f"{APP_NAME} {__version__}")


@cli.command()
@common_options
@click.pass_context
def completions(ctx: click.RichContext) -> None:
    "Print the commands to enable tab completions in your shell (you can eval this)"

    #! consider changing to this:
    # truenas-api bash | sudo tee /usr/share/bash-completion/completions/truenas-api
    # truenas-api zsh | sudo tee /usr/share/zsh/site-functions/_truenas-api
    # truenas-api tcsh | sudo tee /etc/profile.d/truenas-api.csh

    # FIXME: This is probably not guaranteed to work on all platforms

    assert ctx.console is not None
    ctx.console.no_color = True

    command = "bash_source"
    eval_template1 = 'eval "$(_TRUENAS_API_COMPLETE={command} truenas-api)"'
    eval_template2 = (
        'eval "$(_TRUENAS_API_CONDUIT_COMPLETE={command} truenas-api-conduit)"'
    )

    if shell_env := os.environ.get("SHELL"):
        shell = shell_env.split("/")[-1]
        if shell == "zsh":
            command = "zsh_source"
        elif shell == "fish":
            command = "source"
            eval_template1 = "_TRUENAS_API_COMPLETE={command} truenas-api | source"
            eval_template2 = (
                "_TRUENAS_API_CONDUIT_COMPLETE={command} truenas-api-conduit | source"
            )

    ctx.console.print(eval_template1.format(command=command))
    ctx.console.print(eval_template2.format(command=command))

    console_stderr.print(
        f"[italic]Tip: You can eval this command to enable tab completions:[/italic]"
        f"""  [{COLORS.command}]eval $(truenas-api completions)""",
        markup=True,
    )
