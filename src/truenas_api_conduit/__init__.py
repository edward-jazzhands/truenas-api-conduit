from typing import Final
from importlib.metadata import version
import truenas_api_conduit.core.log_setup as log_setup  # <- this is run on import

APP_NAME: Final = "truenas-api-conduit"

__version__ = version(APP_NAME)

__all__ = [
    "APP_NAME",
    "__version__",
    "log_setup",
]
