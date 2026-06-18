"""Exposes one submodule/interface for everything related to
pydantic-settings. Importing of pydantic is delayed until the
Config class is imported from this module.
"""

from .user_config import Config, AppBaseConfig

__all__ = [
    "Config",
    "AppBaseConfig",
]
