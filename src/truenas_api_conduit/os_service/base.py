from typing import assert_never
from abc import ABC, abstractmethod

from truenas_api_conduit.constants import AppEnv, Platform
from truenas_api_conduit.errors import ConduitError


class ServiceError(ConduitError):
    "Base class for service errors."


class BaseService(ABC):

    @abstractmethod
    def install(self) -> None:
        pass

    @abstractmethod
    def uninstall(self) -> None:
        pass

    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def restart(self) -> None:
        pass

    @abstractmethod
    def status(self, forward_stdout: bool = True, suppress_output: bool = False) -> int:
        # Status should print whatever the actual service manager prints to stdout
        # and *then* return the exit code of the command (at least on Linux, not sure
        # if this equally applies to mac and windows)
        pass

    @abstractmethod
    def detect_service(self) -> AppEnv:
        # Needs to check if the service is currently running as an OS service or
        # if it's in standalone mode
        pass

    @abstractmethod
    def logs(self, follow: bool = False, limit: int = 100) -> str | None:
        pass


def get_service_manager(platform: Platform) -> BaseService:
    """A single `get_service_manager()` factory function resolves the correct
    implementation at runtime based on a `Platform` enum determined by the core
    module when the program starts.
    """
    match platform:
        case Platform.LINUX:
            from truenas_api_conduit.os_service.linux import LinuxService

            return LinuxService()
        case Platform.WINDOWS:
            from truenas_api_conduit.os_service.windows import WindowsService

            return WindowsService()
        case Platform.MACOS:
            from truenas_api_conduit.os_service.macos import MacOSService

            return MacOSService()
        case _:
            assert_never(platform)
