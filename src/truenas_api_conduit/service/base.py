from typing import assert_never, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

from truenas_api_conduit import InstallType
from truenas_api_conduit.core import Platform


class BaseService(ABC):

    @abstractmethod
    def install(self, install_type: InstallType) -> None:
        pass

    @abstractmethod
    def uninstall(self) -> None:
        pass

    @abstractmethod
    def start(self, cfg: Config):
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def restart(self) -> None:
        pass

    @abstractmethod
    def status(self, stdout: bool = True) -> int:
        """should return the exit code of the service status command after
        printing it to stdout."""
        pass


def get_service_manager(platform: Platform) -> BaseService:
    """A single `get_service_manager()` factory function resolves the correct
    implementation at runtime based on a `Platform` enum determined by the core
    module when the program starts.
    """
    match platform:
        case Platform.LINUX:
            from .linux import LinuxService

            return LinuxService()
        case Platform.WINDOWS:
            from .windows import WindowsService

            return WindowsService()
        case Platform.MACOS:
            from .macos import MacOSService

            return MacOSService()
        case _:
            assert_never(platform)
