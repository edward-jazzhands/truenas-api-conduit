from .base import BaseService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

import truenas_api_conduit.core as core


class WindowsService(BaseService):

    def install(self) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self) -> None:
        pass

    def status(self, forward_stdout: bool = True, suppress_output: bool = False) -> int:
        return 0

    def detect_service(self) -> core.AppEnv:
        if True:
            return core.AppEnv.OS_SERVICE
        else:
            return core.AppEnv.STANDALONE

    def logs(self, follow: bool = False, limit: int = 100) -> str | None:

        pass
