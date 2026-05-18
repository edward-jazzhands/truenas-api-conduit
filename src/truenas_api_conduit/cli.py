# standard library
import asyncio
import signal
import sys
import logging
from copy import copy

# third-party
# import click
import rich_click as click

# project
import truenas_api_conduit.user_config

log = logging.getLogger(__name__)

# First lets sketch out the commands that we want to have.
#  - launch daemon mode
#  - set API key
#  - make a request


# NOTE: When using click.group() as the main command, it will automatically show
# the --help message when no subcommands are specified.

@click.group()
@click.pass_context
@click.option('-v', '--verbose', count=True)
def cli(ctx: click.Context, verbose: int):
    """TrueNAS API Conduit - Websocket proxy daemon for the TrueNAS API.

    Write more info here when project is finished.
    """

    level = logging.ERROR
    if verbose == 1:
        level = logging.WARNING
    elif verbose == 2:
        level = logging.INFO
    elif verbose >= 3:
        level = logging.DEBUG

    log.setLevel(level)

    log.critical("Always printed to stderr")
    log.error("Always printed to stderr")
    log.warning("only with -v")
    log.info("only with -vv")
    log.debug("only with -vvv")
    
    
    click.echo("always printed to stdout")
    
    choice: bool = click.confirm("Are you sure?", abort=True, err=True)
    click.echo(f"You said {choice}", err=True)
    # asyncio.run(start())

def handle_exit(*_):
    print("\nShutting down.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)


@cli.command()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Launches the daemon mode"""

    # TODO: Implement daemon mode
    pass

@cli.command()
@click.pass_context
def set_api_key(ctx: click.Context) -> None:
    """Sets the API key"""

    # TODO: Implement set API key
    pass

@cli.command()
@click.pass_context
def request(ctx: click.Context) -> None:
    """Makes a request"""

    # TODO: Implement request
    pass
