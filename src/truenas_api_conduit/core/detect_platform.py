import sys

from truenas_api_conduit import APP_NAME
from truenas_api_conduit.core.global_enums import Platform


def detect() -> Platform:

    match sys.platform:
        case "linux":
            return Platform.LINUX
        case "win32":
            return Platform.WINDOWS
        case "darwin":
            return Platform.MACOS
        case _:
            raise RuntimeError(f"Unknown Operating System: {sys.platform}")
