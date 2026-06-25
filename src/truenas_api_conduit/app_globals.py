from truenas_api_conduit.constants import AppEnv
from dataclasses import dataclass


@dataclass
class AppGlobals:
    is_config_frozen: bool = False
    app_env: AppEnv | None = None
    cli_trace: bool = False

    def set_config_frozen(self) -> None:
        app_globals.is_config_frozen = True

    def set_app_env(self, env: AppEnv) -> None:
        if env not in AppEnv or env is None:
            raise ValueError(f"Invalid TRUENAS_APP_ENV: {env}")
        app_globals.app_env = env

    def set_cli_trace(self, trace: bool) -> None:
        app_globals.cli_trace = trace


app_globals: AppGlobals = AppGlobals()
