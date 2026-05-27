import sys
from enum import Enum
from typing import Final
from pathlib import Path
from truenas_api_conduit import APP_NAME

class Platform(Enum):
    LINUX = "linux"
    WINDOWS = "win32"
    MACOS = "darwin"


def detect() -> Platform:

    if sys.platform == "linux":
        return Platform.LINUX
    elif sys.platform == "win32":
        return Platform.WINDOWS
    elif sys.platform == "darwin":
        return Platform.MACOS
    else:
        raise RuntimeError(f"Unknown Operating System: {sys.platform}")
        