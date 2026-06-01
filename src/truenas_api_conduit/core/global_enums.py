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
    # f"http://127.0.0.1:{self.port}{endpoint}",

    RPC = "/rpc"
    STATUS = "/status"
    SHUTDOWN = "/shutdown"
    RESTART = "/restart"
