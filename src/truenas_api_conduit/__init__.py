from typing import Final
from importlib.metadata import version
import truenas_api_conduit.log_setup

APP_NAME: Final = "truenas-api-conduit"

__version__ = version(APP_NAME)

__all__ = [
    "APP_NAME",
    "__version__",
]
