# standard library
import signal
import sys
import logging
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from truenas_api_conduit.user_config import Config

# third-party
import rich_click as click
from click_didyoumean import DYMMixin
from rich.traceback import install

# project
from truenas_api_conduit import __version__, APP_NAME
import truenas_api_conduit.log_setup as log_setup
from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.cli_options_class import CLIOptions

# rich tracebacks
install(console=console_stderr, show_locals=False)

# Rich-click Config
click.rich_click.MAX_WIDTH = 120
click.rich_click.COMMANDS_BEFORE_OPTIONS = True
click.rich_click.THEME = "cargo-modern"
# colorschemes: #~ [default, star, quartz, quartz2, cargo, forest, nord, dracula, solarized]
# theme types: #~ [box, slim, modern, robo, nu]
# nord, dracula, and solarized are "risky" according to the docs.

log = logging.getLogger(__name__)


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


def common_setup(cli_options: CLIOptions) -> Config:

    # NOTE: Remember the root logger starts at WARNING, so the very first thing
    # we always must do is drop it to the user's desired level.
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
        "truenas_host": cli_options.truenas_host,
        "api_key": cli_options.api_key,
    }
    args_dict = {k: v for k, v in to_filter.items() if v is not None}

    # NOTE: Remember that the config file/dir must be ensured before trying to
    # import the user_config module:
    from truenas_api_conduit.setup_app_dir import ensure_config

    ensure_config()

    from truenas_api_conduit.user_config import Config
    import pydantic  #   also lazy to reduce startup time

    try:
        cfg = Config(**args_dict)
    except pydantic.ValidationError as e:
        log.error(f"You have an error in your configuration: {e}")
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
            log.error(
                f"Could not initialize config:  {e} ({e.__class__.__name__}) \n"
                "Raise the log level/verbosity to see more information."
            )
            sys.exit(1)

    log.info("Config loaded")
    log.debug(cfg)
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
#    $ truenas-api --api-key=1234567890 daemon

# This, I believe, is awkward and not how most other CLI frameworks handle this.
# Instead I want these options to be available to all subcommands, like this:
#    $ truenas-api daemon --api-key=1234567890

# In order to achieve this, we need to use these callbacks combined with custom
# option group decorators (below), which we can then re-use across subcommands.
# I researched all the possible ways to solve this problem, and this seems to be
# the most recommended one.


def set_verbose_param(ctx: click.Context, param: click.Parameter, value: int) -> int:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.verbose = value
    return value


def set_truenas_host_param(ctx: click.Context, param: click.Parameter, value: str) -> str:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.truenas_host = value
    return value


def set_key_param(ctx: click.Context, param: click.Parameter, value: str) -> str:
    assert isinstance(ctx.obj, CLIOptions)
    ctx.obj.api_key = value
    return value


api_key_help = """Your TrueNAS API key. Here for convenience, but it is recommended to \
use a secrets manager (best), or set an environment variable named TRUENAS_API_KEY. \
You can also set the api_key field in the config file."""

truenas_host_help = """The address that you use to access the TrueNAS Web UI over HTTPS. \
It is recommended to set this in your config file."""

verbose_help = """Sets the verbosity/logging level. -v for info, -vv for debug, \
-vvv for trace."""


def common_options(f: Callable) -> Callable:
    f = click.option(
        "-v",
        "--verbose",
        count=True,
        callback=set_verbose_param,
        expose_value=False,  # * <-- This is important
        help=verbose_help,
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
        "--api-key", callback=set_key_param, expose_value=False, help=api_key_help
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


@click.group(cls=CustomGroup)
@click.command_panel("Main", commands=["daemon", "request"])
@click.command_panel("Config", commands=["set_key", "config_path"])
@click.pass_context
def cli(ctx: click.Context) -> None:
    """TrueNAS API Conduit - Websocket proxy daemon for the TrueNAS API."""

    ctx.ensure_object(CLIOptions)


@cli.command()
@common_options
@main_commands_options
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Launches the daemon mode. This will hold the websocket connection
    open so that subsequent requests can re-use the same connection."""

    assert isinstance(ctx.obj, CLIOptions)
    cfg = common_setup(ctx.obj)

    log.debug("Config provenance: %s", cfg.provenance)

    from truenas_api_conduit.daemon import start

    start(cfg)


@cli.command()
@common_options
@main_commands_options
@click.pass_context
def request(ctx: click.Context) -> None:
    """Make a request, using the daemon if it's running. Otherwise, the program
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

    # TODO: Implement set API key
    log.debug("Setting API key")
    pass


@cli.command()
@common_options
@click.pass_context
def config_path(ctx: click.Context) -> None:
    """Prints the path to the config file."""

    from truenas_api_conduit.setup_app_dir import CONFIG_PATH

    click.echo(CONFIG_PATH)  # stays clean/pure for piping
    console_stderr.print(f"Created already?: {CONFIG_PATH.exists()}")
