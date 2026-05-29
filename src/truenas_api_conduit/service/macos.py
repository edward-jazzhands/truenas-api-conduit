from .base import BaseService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config
    from truenas_api_conduit.core import InstallType


class MacOSService(BaseService):

    def install(self, install_type: InstallType):
        pass

    def uninstall(self):
        pass

    def start(self, cfg: Config):
        pass

    def stop(self):
        pass

    def restart(self):
        pass

    def status(self):
        pass

    def __call__(self):
        pass
