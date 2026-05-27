from .base import BaseService
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config


class WindowsService(BaseService):

    def install(self):
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