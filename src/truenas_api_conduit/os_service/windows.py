
# standard library
import shutil
import subprocess
import sys
import os
from pathlib import Path
import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

# local
from truenas_api_conduit.constants import (
    APP_NAME,
    SERVICENAME,
    AppEnv,
    XDG_CONFIG_HOME,
    LOCK_FILE,
)
import truenas_api_conduit.core as core
from truenas_api_conduit.i18n import _
from truenas_api_conduit.cli.cli_helpers import cli_print
from truenas_api_conduit.os_service.base import BaseService, ServiceError
from truenas_api_conduit.console import console_stdout  # , console_stderr


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

    def detect_service(self) -> AppEnv:
        if True:
            return AppEnv.OS_SERVICE
        else:
            return AppEnv.STANDALONE

    def logs(self, follow: bool = False, limit: int = 100) -> str | None:

        pass
