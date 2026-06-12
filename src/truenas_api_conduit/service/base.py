from typing import assert_never, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

import truenas_api_conduit.core as core


class ServiceError(Exception):
    "Base class for service errors."


class BaseService(ABC):

    @abstractmethod
    def install(self) -> None:
        pass

    @abstractmethod
    def uninstall(self) -> None:
        pass

    @abstractmethod
    def start(self, cfg: Config) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def restart(self) -> None:
        pass

    @abstractmethod
    def status(self, stdout: bool = True) -> int:
        # Status should print whatever the actual service manager prints to stdout
        # and *then* return the exit code of the command (at least on Linux, not sure
        # if this equally applies to mac and windows)
        pass

    @abstractmethod
    def detect_service(self) -> core.AppEnv:
        # Needs to check if the service is currently running as an OS service or
        # if it's in standalone mode
        pass


def get_service_manager(platform: core.Platform) -> BaseService:
    """A single `get_service_manager()` factory function resolves the correct
    implementation at runtime based on a `Platform` enum determined by the core
    module when the program starts.
    """
    match platform:
        case core.Platform.LINUX:
            from .linux import LinuxService

            return LinuxService()
        case core.Platform.WINDOWS:
            from .windows import WindowsService

            return WindowsService()
        case core.Platform.MACOS:
            from .macos import MacOSService

            return MacOSService()
        case _:
            assert_never(platform)
