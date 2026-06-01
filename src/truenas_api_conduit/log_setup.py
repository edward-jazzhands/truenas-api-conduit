from typing import Final
import logging
import copy
from dataclasses import dataclass
from rich.logging import RichHandler
from truenas_api_conduit.console import console_stderr
from truenas_api_conduit.constants import APP_NAME

TRACE: Final = 5
PACKAGE_NAME: Final = APP_NAME.replace("-", "_")


class TraceLogger(logging.Logger):
    def trace(self, message, *args, **kwargs):
        if self.isEnabledFor(TRACE):
            self._log(TRACE, message, args, **kwargs)
            #    Note we purposefully ^^^^ do not unpack args here.


class AppFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(PACKAGE_NAME)


class LibraryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(PACKAGE_NAME)


class PackageNameFormatter(logging.Formatter):

    def __init__(self):
        super().__init__("[grey50][LIB %(package)s] %(message)s", "[%X]")

    def format(self, record):
        record.package = record.name.split(".")[0]
        return super().format(record)


def make_rich_handler(
    show_time: bool = False,
    show_path: bool = False,
) -> RichHandler:

    return RichHandler(
        console=console_stderr,
        markup=True,
        show_time=show_time,
        show_path=show_path,
        omit_repeated_times=False,
    )


@dataclass(frozen=True)
class HandlersStorage:
    normal: RichHandler
    debug: RichHandler
    libraries: RichHandler


_handlers_storage: HandlersStorage | None = None


def init_logging():

    logging.addLevelName(TRACE, "TRACE")
    logging.setLoggerClass(TraceLogger)

    formatter = logging.Formatter("%(message)s", datefmt="[%X]")
    libformatter = PackageNameFormatter()

    rich_handler_normal = make_rich_handler(show_time=False, show_path=False)
    rich_handler_normal.addFilter(AppFilter())
    rich_handler_normal.setFormatter(formatter)

    rich_handler_debug = make_rich_handler(show_time=True, show_path=True)
    rich_handler_debug.addFilter(AppFilter())
    rich_handler_debug.setFormatter(formatter)

    rich_handler_libraries = make_rich_handler(show_time=True, show_path=True)
    rich_handler_libraries.addFilter(LibraryFilter())
    rich_handler_libraries.setFormatter(libformatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    root_logger.addHandler(rich_handler_normal)

    global _handlers_storage
    _handlers_storage = HandlersStorage(
        normal=rich_handler_normal,
        debug=rich_handler_debug,
        libraries=rich_handler_libraries,
    )


def set_log_level(level: int) -> None:

    if _handlers_storage is None:
        raise RuntimeError("Logging not initialized")

    logging.getLogger().setLevel(level)

    if level <= TRACE:
        logging.getLogger().handlers = [
            _handlers_storage.debug,
            _handlers_storage.libraries,
        ]
    elif level <= logging.DEBUG:
        logging.getLogger().handlers = [_handlers_storage.debug]
    else:
        logging.getLogger().handlers = [_handlers_storage.normal]


def enable_timestamps_on_normal() -> None:

    if _handlers_storage is None:
        raise RuntimeError("Logging not initialized")

    if not logging.getLogger().level >= 20:
        raise RuntimeError("Can only toggle timestamps when set to info or higher")

    normal_with_time = make_rich_handler(show_time=True, show_path=False)
    normal_with_time.addFilter(AppFilter())
    formatter = logging.Formatter("%(message)s", datefmt="[%X]")
    normal_with_time.setFormatter(formatter)
    logging.getLogger().handlers = [normal_with_time]
