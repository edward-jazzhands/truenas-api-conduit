# standard library
import sys
import os
import json
from typing import Any, Callable, assert_never

# third-party
import rich_click as click
from click_didyoumean import DYMMixin

# project
from truenas_api_conduit.constants import (
    __version__,
    APP_NAME,
    SERVICENAME,
    COLORS,
    ENV,
)
import truenas_api_conduit.core as core
from truenas_api_conduit.app_globals import app_globals
from truenas_api_conduit.constants import AppEnv, Endpoints, PLATFORM, CRYPT_KEY_PATH
from truenas_api_conduit.errors import ConduitError
from truenas_api_conduit.console import console_stderr, console_stdout
from truenas_api_conduit.cli.cli_helpers import (
    CLIOptions,
    cli_print_setup,
    make_usage_error_panel,
    make_success_panel,
    prompt_for_config,
)
from truenas_api_conduit.cli.request_helper import get_request_helper
from truenas_api_conduit.os_service import get_service_manager, ServiceError
import truenas_api_conduit.cli.helps as helps


# Rich-click Config
click.rich_click.COMMANDS_BEFORE_OPTIONS = True
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.THEME = "cargo-modern"
# colorschemes: #~ [default, star, quartz, quartz2, cargo, forest, nord, dracula, solarized]
# theme types: #~ [box, slim, modern, robo, nu]
# nord, dracula, and solarized are "risky" according to the docs.

click.rich_click
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
        help=helps.verbose_help,
    )(f)
    f = click.option(
        "-nc",
        "--no-color",
        is_flag=True,
        default=None,
        callback=set_no_color_param,
        expose_value=False,
        help=helps.no_color_help,
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
        help=helps.pretty_help,
    )(f)
    return f


class CustomGroup(DYMMixin, click.RichGroup):  # Adds click-didyoumean
    pass


main_commands = [
    "request",
    "install",
    "uninstall",
    "start",
    "stop",
    "restart",
    "lock",
    "unlock",
    "status",
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

# Rich-Click will look for the "rich_console" key in the context dictionary
# ! this console will be used for all the CLI stdout/help menus (NOT stderr)
# ! confirm this is true.
context = {
    "rich_console": console_stdout,
}

# NOTE: When using click.group() as the main command, it will automatically show
# the --help message when no subcommands are specified.


@click.group(cls=CustomGroup, context_settings=context, help=helps.main_help)
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

    # The setup functions (cli_print_setup, config_setup) cannot be
    # run here, because they would not catch options that were passed into
    # the subcommands. If global options are set on the main command, they'll
    # be passed through in the click context, so that the subcommand gets
    # the full context when it does the setups.


log_choices = ["trace", "debug", "info", "warning", "error"]

@cli.command(help=helps.start_help, short_help=helps.start_help_short)
@click.option(
    "-s", "--standalone", is_flag=True, default=False, help=helps.standalone_help
)
@click.option("-l", "--locked", is_flag=True, default=None, help=helps.locked_help)
@click.option("-a", "--api-key", is_flag=True, default=None, help=helps.api_key_help)
@click.option("-t", "--truenas-address", help=helps.truenas_address_help)
@click.option("-h", "--host", help=helps.conduit_host_help)
@click.option(
    "-log", "--log-level", help=helps.log_level_help, type=click.Choice(log_choices)
)
@click.option(
    "-vc", "--validate-certs", is_flag=True, default=None, help=helps.validate_certs_help
)
@common_options
@click.pass_context
def start(
    ctx: click.RichContext,
    standalone: bool,  # <- this is the only one that's not a config option
    locked: bool | None = None,
    api_key: bool | None = None,
    truenas_address: str | None = None,
    host: str | None = None,
    validate_certs: bool | None = None,
    log_level: str | None = None,
) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    prompt_for_config()

    # standalone + locked = OK
    # standalone + api_key = OK
    # standalone + truenas_address = OK
    # locked + api_key = ERROR
    # locked + truenas_address = ERROR? (might be ok?)
    # api_key + truenas_address = OK

    standalone_dict = {
        "api-key": api_key,
        "truenas-address": truenas_address,
        "host": host,
        "validate-certs": validate_certs,
        "log-level": log_level,
    }

    if standalone:
        for key, value in standalone_dict.items():
            if value:
                raise click.UsageError(
                    f"You cannot use the --{key} option with --standalone"
                )

    #! TODO: test if this is necessary
    # if api_key and locked:
    #     raise click.UsageError(
    #         "You cannot use the --api-key and --locked options together"
    #     )

    assert isinstance(ctx.obj, CLIOptions)

    ctx.obj.start_locked = locked
    ctx.obj.api_key = api_key
    ctx.obj.truenas_address = truenas_address
    ctx.obj.conduit_host = host
    ctx.obj.validate_certs = validate_certs
    ctx.obj.log_level = log_level

    from truenas_api_conduit.cli.config_setup import config_setup, config_setup_locked

    if standalone:

        if locked:
            cli_print.info("Starting service in foreground, and in locked mode")
            cfg = config_setup_locked(ctx.obj)
        else:
            cli_print.info("Starting service in foreground")
            cfg = config_setup(ctx.obj)

        cfg_dump = cfg.model_dump_json(context={"unmask": True})

        os.environ["TRUENAS_APP_ENV"] = AppEnv.STANDALONE.value
        if locked:
            os.environ["TRUENAS_START_LOCKED"] = "true"

        # We know the daemon entrypoint will always be in the same directory as the
        # current python executable. This is just how python packaging works. The daemon
        # and the CLI are both included in the package so they will both always be beside
        # each other. That's true in an isolated venv or in a system/global install.
        # Even if its installed as a "UV tool" and has an executable placed in /usr/bin
        # or something, that's actually just a shortcut to the real one in the program venv,
        # which, UV will store in its tools directory.
        venv_bin_dir = os.path.dirname(sys.executable)
        daemon_path = os.path.join(venv_bin_dir, SERVICENAME)

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

            # * This shall henceforth be known as The execv stdin Chad Swap

            # The new program inherits all open file descriptors, including fd 0,
            # which it now sees as normal stdin
            os.execv(daemon_path, [SERVICENAME])
        except Exception as e:
            raise ConduitError("Error restarting the service") from e

    else:
        cli_print.info("Telling OS to start the service")

        service = get_service_manager(PLATFORM)
        cli_print.info("Service: {service}".format(service=service))

        # service manager will check if its installed and exit if not
        try:
            service.start()
        # let the service errors bubble up to the top level
        except ServiceError:
            pass
        except Exception as e:
            raise ConduitError("Unexpected error while starting the service") from e
        else:
            ctx.console.print("TrueNAS API Conduit service started successfully")


@cli.command(help=helps.install_help, short_help=helps.install_help_short)
@common_options
@click.pass_context
def install(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    if not click.confirm(
        f"This will install the {APP_NAME} service. Continue?", default=True
    ):
        console_stderr.print("Cancelled")
        sys.exit(1)

    from truenas_api_conduit.core import ensure_app_dirs

    ensure_app_dirs()

    service = get_service_manager(PLATFORM)

    try:
        service.install()
    except ServiceError:
        pass
    except Exception as e:
        raise ConduitError("Unexpected error while installing the service") from e
    else:
        ctx.console.print("TrueNAS API Conduit service installed successfully")


@cli.command(help=helps.uninstall_help)
@common_options
@click.pass_context
def uninstall(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    if not click.confirm(
        f"This will uninstall the {APP_NAME} service. Continue?", default=True
    ):
        console_stderr.print("Cancelled")
        sys.exit(1)

    from truenas_api_conduit.core import ensure_app_dirs

    ensure_app_dirs()

    service = get_service_manager(PLATFORM)

    try:
        service.uninstall()
    except ServiceError:
        pass
    except Exception as e:
        raise ConduitError("Unexpected error while uninstalling the service") from e
    else:
        ctx.console.print("TrueNAS API Conduit service was uninstalled")


@cli.command(help=helps.request_help, short_help=helps.request_help_short)
@click.argument("method", help="The method to call (ex: system.info)", required=True)
@click.option("--params", "-p", help="The params to pass to the method")
@click.option(
    "-f",
    "--filter",
    "filters",
    nargs=3,
    multiple=True,
    metavar="FIELD OP VALUE",
    help=helps.filters_help,
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

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    request_helper = get_request_helper()
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
        cli_print.debug("Raw params: {params}".format(params=params))
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
        cli_print.info("Method: {method} | Params list: {params_list}".format(method=method, params_list=params_list))

    if filters_list or params_list:
        combined = [filters_list + params_list]
    else:
        combined = []
    cli_print.info("Full request params: {combined}".format(combined=combined))

    response = request_helper(Endpoints.REQUEST, {"method": method, "params": combined})
    if ctx.obj.pretty:
        jsons = json.loads(response.text)
        ctx.console.print(json.dumps(jsons, indent=2), soft_wrap=True)
    else:
        ctx.console.print(response.text, soft_wrap=True)


@cli.command(help=helps.stop_help)
@click.option("-d", "--direct", is_flag=True, default=False, help=helps.stop_direct_help)
@common_options
@click.pass_context
def stop(ctx: click.RichContext, direct: bool = False) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    # TWO WAYS TO STOP
    # 1) Send the service a stop request directly
    # 2) Tell service manager to stop the service

    service = get_service_manager(PLATFORM)

    # This will tell us if the service is installed or not
    detect = service.detect_service()
    cli_print.info("Service mode is: {detect}".format(detect=detect))

    if (detect == AppEnv.STANDALONE) or direct:
        # Option 1: Sending a request
        request_helper = get_request_helper()
        if not request_helper:
            console_stderr.print(
                make_usage_error_panel("TrueNAS API Conduit service is not running")
            )
            sys.exit(1)

        response = request_helper(Endpoints.STOP, {})  # needs empty dict to POST
        if ctx.obj.pretty:
            jsons = json.loads(response.text)
            ctx.console.print(json.dumps(jsons, indent=2), soft_wrap=True)
        else:
            ctx.console.print(response.text, soft_wrap=True)

    elif detect == AppEnv.OS_SERVICE:
        try:
            service.stop()
        except ServiceError:
            pass
        except Exception as e:
            raise ConduitError("Unexpected error while stopping the service") from e

    elif detect == AppEnv.DOCKER:
        err_panel = make_usage_error_panel(
            "You cannot stop the service in Docker mode. Stop the docker container instead."
        )
        console_stderr.print(err_panel)
        sys.exit(1)
    elif detect == AppEnv.CLI:
        raise RuntimeError("The service detection can not return that it detects 'CLI' mode")
    else:
        assert_never(detect)


@cli.command(help=helps.restart_help, short_help=helps.restart_help_short)
@click.option("-d", "--direct", is_flag=True, default=False, help=helps.direct_help)
@click.option("-h", "--hot", is_flag=True, default=False, help=helps.hot_restart_help)
@common_options
@click.pass_context
def restart(ctx: click.RichContext, direct: bool = False, hot: bool = False) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    # TWO WAYS TO STOP
    # 1) Send the service a stop request
    # 2) Tell service manager to stop the service

    service = get_service_manager(PLATFORM)
    detect = service.detect_service()
    cli_print.info("Service mode is: {detect}".format(detect=detect))

    if (detect == AppEnv.STANDALONE) or direct or hot:
        request_helper = get_request_helper()
        if not request_helper:
            console_stderr.print(
                make_usage_error_panel("TrueNAS API Conduit service is not running")
            )
            sys.exit(1)
        response = request_helper(Endpoints.RESTART, {"hot": hot})  # empty dict to post

        if ctx.obj.pretty:
            jsons = json.loads(response.text)
            ctx.console.print(json.dumps(jsons, indent=2), soft_wrap=True)
        else:
            ctx.console.print(response.text, soft_wrap=True)

    elif detect == AppEnv.OS_SERVICE:

        if service.status(forward_stdout=False, suppress_output=True) == 3:
            cli_print.warning("Service is currently stopped, this will start it")
        try:
            service.restart()
        except ServiceError:
            pass
        except Exception as e:
            raise ConduitError("Unexpected error while restarting the service") from e

    elif detect == AppEnv.DOCKER:
        err_panel = make_usage_error_panel(
            "You cannot restart the service in Docker mode. Restart the docker container instead."
        )
        console_stderr.print(err_panel)
        sys.exit(1)
    elif detect == AppEnv.CLI:
        raise RuntimeError("The service detection can not return that it detects 'CLI' mode")
    else:
        assert_never(detect)


@cli.command(help=helps.lock_help, short_help=helps.lock_help_short)
@common_options
@click.pass_context
def lock(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    request_helper = get_request_helper()
    if not request_helper:
        console_stderr.print(
            make_usage_error_panel("TrueNAS API Conduit service is not running")
        )
        sys.exit(1)

    response = request_helper(Endpoints.LOCK, {})  # empty dict to post
    if ctx.obj.pretty:
        jsons = json.loads(response.text)
        ctx.console.print(json.dumps(jsons, indent=2), soft_wrap=True)
    else:
        ctx.console.print(response.text, soft_wrap=True)


@cli.command(help=helps.unlock_help, short_help=helps.unlock_help_short)
@common_options
@click.pass_context
def unlock(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    request_helper = get_request_helper()
    if not request_helper:
        console_stderr.print(
            make_usage_error_panel("TrueNAS API Conduit service is not running")
        )
        sys.exit(1)

    crypt_key = click.prompt("Enter your encryption password", hide_input=True)

    response = request_helper(Endpoints.UNLOCK, {"crypt_key": crypt_key})
    if ctx.obj.pretty:
        jsons = json.loads(response.text)
        ctx.console.print(json.dumps(jsons, indent=2), soft_wrap=True)
    else:
        ctx.console.print(response.text, soft_wrap=True)


@cli.command(help=helps.status_help, short_help=helps.status_help_short)
@click.option(
    "-sys", "--system", is_flag=True, default=False, help=helps.system_status_help
)
@request_options
@common_options
@click.pass_context
def status(ctx: click.RichContext, system: bool = False) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    # TWO WAYS TO GET THE STATUS
    # 1) Send the service a status request
    # 2) Ask the service manager

    running = False
    if not system:
        # 1: Sending a request
        request_helper = get_request_helper()
        if request_helper:
            response = request_helper(Endpoints.STATUS)
            if ctx.obj.pretty:
                jsons = json.loads(response.text)
                ctx.console.print(json.dumps(jsons, indent=2), soft_wrap=True)
                running = True
            else:
                ctx.console.print(response.text, soft_wrap=True)
                running = True
        else:  # no request helper
            ctx.console.print("TrueNAS API Conduit service is not running")
            # NOTE: here we don't immediately exit, proceed to check if the
            # service is installed

    if system or not running:
        # 2: Asking the service manager
        service = get_service_manager(PLATFORM)
        detect = service.detect_service()
        cli_print.info("Service mode is: {detect}".format(detect=detect))

        if system and (detect != AppEnv.OS_SERVICE):
            console_stderr.print(
                make_usage_error_panel(
                    "--system can only be used with the service in OS mode"
                )
            )
            sys.exit(1)

        if detect == AppEnv.STANDALONE:
            cli_print.warning("The service last reported running in standalone mode")
            sys.exit(1)

        try:
            service.status(forward_stdout=system)
        except ServiceError:
            pass
        except Exception as e:
            raise ConduitError(
                "Unexpected error while checking status of the service"
            ) from e


@cli.command(help=helps.logs_help, short_help=helps.logs_helps_short)
@click.option("-f", "--follow", is_flag=True, default=False, help=helps.follow_help)
@click.option(
    "-l", "--limit", type=int, default=100, help=helps.limit_help, show_default=True
)
@common_options
@click.pass_context
def logs(ctx: click.RichContext, limit: int, follow: bool = False) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    service = get_service_manager(PLATFORM)
    detect = service.detect_service()
    cli_print.info("Service mode is: {detect}".format(detect=detect))

    if detect != AppEnv.OS_SERVICE:
        console_stderr.print(
            make_usage_error_panel("You can only get the logs for the service in OS mode")
        )
        sys.exit(1)

    try:
        logs = service.logs(follow=follow, limit=limit)
    except ServiceError:
        pass
    except Exception as e:
        raise ConduitError("Unexpected error while checking status of the service") from e
    else:
        if logs and not follow:
            ctx.console.print(logs)
        else:
            ctx.console.print("No logs found")


@cli.command(help=helps.set_key_help, short_help=helps.set_key_help_short)
@click.option("-d", "--delete", is_flag=True, default=False, help=helps.delete_help)
@click.option(
    "-dc", "--del-crypt", is_flag=True, default=False, help=helps.del_crypt_help
)
@click.option("-s", "--show", is_flag=True, default=False, help=helps.show_help)
@click.option("-e", "--encrypted", is_flag=True, default=False, help=helps.encrypted_help)
@common_options
@click.pass_context
def set_key(
    ctx: click.RichContext,
    delete: bool = False,
    del_crypt: bool = False,
    show: bool = False,
    encrypted: bool = False,
) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print = cli_print_setup(ctx.obj)

    if delete and show:
        raise click.UsageError("You cannot specify both --delete and --show")
    if del_crypt and show:
        raise click.UsageError("You cannot specify both --del-crypt and --show")

    prompt_for_config()

    import keyring
    import keyring.errors as kr_errs
    import keyring.backend
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
    cli_print.debug(f"Available keyring backends: {[k.name for k in all_keyrings]}")

    current_backend = keyring.get_keyring()
    cli_print.debug(f"Current keyring backend: {current_backend.name}")

    service = APP_NAME
    username = "api_key"

    action_desc = "<action>"
    actions: list[str] = []
    try:
        if delete or del_crypt:
            if delete:
                cli_print.info("Deleting API key from '{current_backend}'".format(current_backend=current_backend.name))
                action_desc = "delete API key"
                keyring.delete_password(service, username)
                cli_print.debug("Deleted API key from keyring")
                actions.append(action_desc)
            if del_crypt:
                action_desc = "delete crypt key file"
                if not CRYPT_KEY_PATH.exists():
                    if delete:
                        cli_print.error(f"No crypt key file found ({CRYPT_KEY_PATH})")
                    else:
                        ctx.console.print(
                            make_usage_error_panel(
                                "No crypt key file found", "Keyring Error"
                            )
                        )
                else:
                    cli_print.info("Deleting crypt key file")
                    action_desc = "delete crypt key file"
                    CRYPT_KEY_PATH.unlink()
                    cli_print.debug(f"Deleted crypt key file ({CRYPT_KEY_PATH})")
                    actions.append(action_desc)
        elif show:
            cli_print.info("Showing API key from '{current_backend}'".format(current_backend=current_backend.name))
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
            cli_print.info("Setting API key in '{current_backend}'".format(current_backend=current_backend.name))
            cli_print.warning("This will overwrite any existing key you have set")
            action_desc = "set API key"
            api_key = click.prompt("Enter your TrueNAS API key", hide_input=True)
            keyring.set_password(service, username, api_key)
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
            raise ConduitError("Unexpected error while setting API key") from e
    except (
        kr_errs.KeyringError,
        kr_errs.PasswordSetError,
        kr_errs.PasswordDeleteError,
    ) as e:
        cli_print.error("Keyring error: {e}".format(e=e))
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
        err_string = "Failed keyring action: {action} ({cls_name}) | {err}".format(
            action=action_desc,
            cls_name=e.__class__.__name__,
            err=e,
        )
        raise ConduitError(err_string) from e
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


@cli.command(help=helps.config_help)
@click.option(
    "-p", "--print-path", is_flag=True, default=False, help=helps.print_path_help
)
@click.option(
    "-c", "--print-config", is_flag=True, default=False, help=helps.print_config_help
)
@click.option("-u", "--unmask", is_flag=True, default=False, help=helps.unmask_help)
@common_options
@click.pass_context
def config(
    ctx: click.RichContext,
    print_path: bool = False,
    print_config: bool = False,
    unmask: bool = False,
) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    if print_path and print_config:
        raise click.UsageError("You cannot specify both --print-path and --print-config")

    if unmask and not print_config:
        raise click.UsageError("--unmask must be used with --print-config")

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
        from truenas_api_conduit.cli.config_setup import config_setup

        cfg = config_setup(ctx.obj, unmask=unmask)
        json_dict = cfg.model_dump_json(indent=2, context={"unmask": unmask})

        ctx.console.print(json_dict, soft_wrap=True)
        if ctx.obj.verbose == 0:
            console_stderr.print(
                "\n[italic]Tip: set verbosity to see provenance[/italic]",
                markup=True,
            )
    else:
        editor = os.environ.get("EDITOR")
        if editor:
            console_stderr.print(
                "Remember you must restart the service to apply any changes",
                style="italic",
            )
            # we want execvp here so it scans PATH for the editor by name
            os.execvp(editor, [editor, core.CONFIG_PATH])
        else:
            err_string = (
                f"No editor set. Set the [{COLORS.envvar}]$EDITOR[default] "
                "environment variable"
            )
            console_stderr.print(make_usage_error_panel(err_string), "Config Error")
            sys.exit(1)


@cli.command(help=helps.cheatsheet_help)
@common_options
@click.pass_context
def cheatsheet(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    from truenas_api_conduit.cli.cheatsheet import get_tables

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


@cli.command(help=helps.reference_help)
@common_options
@click.pass_context
def reference(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    prompt_for_config()

    from truenas_api_conduit.cli.config_setup import config_setup

    cfg = config_setup(ctx.obj, unmask=False)

    ctx.console.print(f"https://{cfg.truenas_address}/api/docs/current")


@cli.command(help=helps.version_help)
@common_options
@click.pass_context
def version(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    # TODO: This should have some method of pinning which version of the TrueNAS
    # API its written for.

    ctx.console.print(f"{APP_NAME} {__version__}")


@cli.command(help=helps.completions_help)
@click.argument(
    "shell",
    required=False,
    type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False),
)
@common_options
@click.pass_context
def completions(ctx: click.RichContext, shell: str | None) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

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


@cli.command(help=helps.env_help)
@common_options
@click.pass_context
def env(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)
    assert ctx.console is not None
    cli_print_setup(ctx.obj)

    for env in ENV.values():
        ctx.console.print(f"{env}: {os.environ.get(env)}")


def error_handler(err_string: str, e: BaseException):

    console_stderr.print(err_string)
    if app_globals.cli_trace:
        raise e
    else:
        sys.exit(1)


def entrypoint() -> None:

    try:
        cli()
    except click.UsageError:
        pass
    except json.JSONDecodeError as e:
        console_stderr.print(
            "Response from server is not valid JSON. Disable pretty "
            "printing to see the raw response."
        )
        error_handler(str(e), e)
    except ServiceError as e:
        err_string = "Encountered an error while controlling the service.\n"
        err_string = core.examine_error(e)
        error_handler(err_string, e)
    except ConduitError as e:
        err_string = (
            "An unknown error occurred but was caught. The program should exit safely.\n"
            "The error which caused this:\n\n"
        )
        err_string += core.examine_error(e)
        error_handler(err_string, e)
    except OSError as e:
        err_string = (
            "Encountered an operating system error. The error which caused this:\n\n"
        )
        err_string = core.examine_error(e)
        error_handler(err_string, e)
    except Exception as e:
        console_stderr.print(
            "Uncaught exception reached top-level exception handler."
            "This usually indicates a software bug or an unexpected condition the "
            "application could not safely handle. "
            "The application will now exit.\n\n"
        )
        error_handler(str(e), e)


if __name__ == "__main__":
    entrypoint()
