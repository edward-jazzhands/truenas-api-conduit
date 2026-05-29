from typing import Final
from importlib.metadata import version

APP_NAME: Final = "truenas-api-conduit"

__version__ = version(APP_NAME)
