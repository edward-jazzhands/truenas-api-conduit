from typing import Final
import logging
import os
import sys
from dataclasses import dataclass
from rich.logging import RichHandler
from truenas_api_conduit.console import console_stderr, console_stdout

# from truenas_api_conduit import APP_NAME # cant import this here, circular import

TRACE: Final[int] = 5
PACKAGE_NAME: Final[str] = "truenas_api_conduit"
STARTING_LEVEL: Final[int] = logging.WARNING


__all__ = ["logging_manager"]


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

    def __init__(self, rich: bool = True):
        msg = "[LIB %(package)s] %(message)s"
        if rich:
            msg = "[grey50]" + msg
        else:
            msg = "%(levelname)s - " + msg
        super().__init__(msg, "[%X]")

    def format(self, record):
        record.package = record.name.split(".")[0]
        return super().format(record)


def make_rich_handler(
    show_time: bool = False,
    show_path: bool = False,
    stdout: bool = True,
) -> RichHandler:

    return RichHandler(
        console=console_stdout if stdout else console_stderr,
        markup=True,
        show_time=show_time,
        show_path=show_path,
        omit_repeated_times=False,
    )


@dataclass()
class HandlersStorage:
    normal: RichHandler | None = None
    debug: RichHandler | None = None
    libraries: RichHandler | None = None
    stream: logging.StreamHandler | None = None
    libs_stream: logging.StreamHandler | None = None


class LoggingManager:

    def __init__(self):
        self.handlers_storage: HandlersStorage = HandlersStorage()
        self.os_or_docker: bool = False
        self.service: bool = False
        self.app_env: str | None = None

        logging.addLevelName(TRACE, "TRACE")
        logging.setLoggerClass(TraceLogger)

        self.formatter = logging.Formatter("%(message)s", datefmt="[%X]")
        self.libformatter = PackageNameFormatter()

        self.streamformatter = logging.Formatter("%(levelname)s - %(message)s")
        self.streamlibformatter = PackageNameFormatter(rich=False)

        # we can't check if "standalone" was set directly because if we are in standalone
        # then it wouldn't be set at this point. But if we are in OS or docker mode,
        # we know it WILL be set at this point (its set before running)
        if os.environ.get("TRUENAS_APP_ENV") in ("os_service", "docker"):
            self.os_or_docker = True

    def init_logging(self, service: bool = False):
        """service is used to distinguish if this is being run by the CLI or
        by the service entrypoint.

        service is FALSE (from the CLI):
        - logs are sent to stderr

        service is TRUE:
        - logs are sent to stdout instead of stderr
        - if in standalone, timestamps + file:lineno are enabled for all levels
        - if in OS or docker, timestamps + file:lineno are disabled
        """

        service_standalone = False
        if service:
            if self.os_or_docker:
                # this means service entrypoint in OS or docker mode
                # - timestamps get disabled for everything
                # - file:lineno get disabled for everything

                # The OS or docker will add their own timestamps, and module/lineno
                # is only relevant to development. We also can't use the
                # RichHandler because how it works doesn't translate well to system
                # loggers, it needs to pre-allocate space for its virtual console.
                stream_handler = logging.StreamHandler(stream=sys.stdout)
                stream_handler.addFilter(AppFilter())
                stream_handler.setFormatter(self.streamformatter)

                libs_stream_handler = logging.StreamHandler(stream=sys.stdout)
                libs_stream_handler.addFilter(LibraryFilter())
                libs_stream_handler.setFormatter(self.streamlibformatter)

                self.handlers_storage.stream = stream_handler
                self.handlers_storage.libs_stream = libs_stream_handler

            else:
                service_standalone = True

        if service_standalone or not service:

            # service_standalone:
            # - timestamps get enabled for everything
            # - file:lineno is enabled for debug/trace

            # not service: this means the init call came from the CLI
            # - timestamps are enabled for debug/trace
            # - file:lineno is enabled for debug/trace

            rich_handler_normal = make_rich_handler(
                show_time=service_standalone, show_path=False, stdout=service
            )
            rich_handler_normal.addFilter(AppFilter())
            rich_handler_normal.setFormatter(self.formatter)

            rich_handler_debug = make_rich_handler(
                show_time=True, show_path=True, stdout=service
            )
            rich_handler_debug.addFilter(AppFilter())
            rich_handler_debug.setFormatter(self.formatter)

            rich_handler_libraries = make_rich_handler(
                show_time=True, show_path=True, stdout=service
            )
            rich_handler_libraries.addFilter(LibraryFilter())
            rich_handler_libraries.setFormatter(self.libformatter)

            root_logger = logging.getLogger()
            root_logger.setLevel(STARTING_LEVEL)
            root_logger.addHandler(rich_handler_normal)

            self.handlers_storage.normal = rich_handler_normal
            self.handlers_storage.debug = rich_handler_debug
            self.handlers_storage.libraries = rich_handler_libraries

    def set_log_level(self, level: int) -> None:

        if self.handlers_storage is None:
            raise RuntimeError("Logging not initialized")

        logging.getLogger().setLevel(level)

        if self.os_or_docker:
            assert self.handlers_storage.stream is not None
            assert self.handlers_storage.libs_stream is not None

            if level <= TRACE:
                logging.getLogger().handlers = [
                    self.handlers_storage.stream,
                    self.handlers_storage.libs_stream,
                ]
            else:
                logging.getLogger().handlers = [self.handlers_storage.stream]

        else:
            assert self.handlers_storage.debug is not None
            assert self.handlers_storage.libraries is not None
            assert self.handlers_storage.normal is not None

            if level <= TRACE:
                logging.getLogger().handlers = [
                    self.handlers_storage.debug,
                    self.handlers_storage.libraries,
                ]
            elif level <= logging.DEBUG:
                logging.getLogger().handlers = [self.handlers_storage.debug]
            else:
                logging.getLogger().handlers = [self.handlers_storage.normal]

    def enable_timestamps(self) -> None:
        """used by the service to enable timestamps if on info or higher
        (ONLY for standalone mode - uses RichHandler)"""

        if self.handlers_storage is None:
            raise RuntimeError("Logging not initialized")

        if not logging.getLogger().level >= 20:
            raise RuntimeError("Can only toggle extra data when set to info or higher")

        if self.os_or_docker:
            raise RuntimeError("Can only toggle extra data when in standalone mode")

        normal_with_xtra = make_rich_handler(show_time=True, show_path=False, stdout=True)
        normal_with_xtra.addFilter(AppFilter())
        formatter = logging.Formatter("%(message)s", datefmt="[%X]")
        normal_with_xtra.setFormatter(formatter)
        logging.getLogger().handlers = [normal_with_xtra]


logging_manager = LoggingManager()
