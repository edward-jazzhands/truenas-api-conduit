from enum import Enum


class Platform(Enum):
    LINUX = "linux"
    WINDOWS = "win32"
    MACOS = "darwin"


class InstallType(Enum):
    USER = "user"
    SYSTEM = "system"
    PACKAGE = "package"
