from truenas_api_conduit.core import AppEnv

is_config_frozen: bool = False
app_env: AppEnv | None = None


def set_config_frozen() -> None:
    global is_config_frozen
    is_config_frozen = True


def set_app_env(env: AppEnv) -> None:
    if env not in AppEnv or env is None:
        raise ValueError(f"Invalid TRUENAS_APP_ENV: {env}")
    global app_env
    app_env = env
