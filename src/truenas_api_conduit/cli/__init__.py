import signal
import sys

from truenas_api_conduit.log_setup import logging_manager_factory
from truenas_api_conduit.cli.cli_helpers import cli_print
from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.constants import AppEnv
from truenas_api_conduit.app_globals import app_globals


if app_globals.app_env == AppEnv.OS_SERVICE or app_globals.app_env == AppEnv.DOCKER:
    raise RuntimeError("The CLI package was initialized from the wrong entrypoint")


def handle_exit(*__):
    console_stderr.print("\nCancelling.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)


app_globals.set_app_env(AppEnv.CLI)
logging_manager = logging_manager_factory.get_logging_manager(app_env=AppEnv.CLI)
logging_manager.init_logging(printer=cli_print)