import logging
from rich.logging import RichHandler
from truenas_api_conduit.console import console_stderr


class TraceLogger(logging.Logger):

    def trace(self, message, *args, **kwargs):
        if self.isEnabledFor(TRACE):
            self._log(TRACE, message, args, **kwargs)
            #    Note we purposefully ^^^^ do not unpack args here.


TRACE = 5
logging.addLevelName(TRACE, "TRACE")
logging.setLoggerClass(TraceLogger)

rich_handler_normal = RichHandler(
    console=console_stderr,
    markup=True,
    show_time=False,
    show_path=False,
)

rich_handler_debug = RichHandler(
    console=console_stderr,
    markup=True,
    show_time=True,
    show_path=True,
    omit_repeated_times=False,
)

FORMAT = "%(message)s"
logging.basicConfig(
    level="WARNING", format=FORMAT, datefmt="[%X]", handlers=[rich_handler_normal]
)

log = logging.getLogger(__name__)


def set_log_level(level: int) -> None:
    """Set level for the root logger.
    Also enables time and path when level is DEBUG or TRACE."""

    logging.getLogger().setLevel(level)
    if level <= logging.DEBUG:  #   also for trace
        # swap the handler for the debug handler
        logging.getLogger().handlers = [rich_handler_debug]
    else:
        logging.getLogger().handlers = [rich_handler_normal]
    log.debug(f"Changed log level to {level}")
