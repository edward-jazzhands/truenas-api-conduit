from truenas_api_conduit.constants import APP_NAME, __version__
import truenas_api_conduit.log_setup as log_setup  # <- this is run on import

__all__ = [
    "APP_NAME",
    "__version__",
    "log_setup",
]
