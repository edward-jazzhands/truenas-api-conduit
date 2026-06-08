from enum import Enum, StrEnum


class Platform(Enum):
    LINUX = "linux"
    WINDOWS = "win32"
    MACOS = "darwin"


class InstallType(Enum):
    USER = "user"
    SYSTEM = "system"
    PACKAGE = "package"


class Endpoints(StrEnum):
    # this is a string enum because its used to build the URL like this:
    # f"http://{self.address}:{self.port}{endpoint}",

    REQUEST = "/request"
    STATUS = "/status"
    SHUTDOWN = "/shutdown"
    RESTART = "/restart"
