is_config_frozen: bool = False


def set_config_frozen() -> None:
    global is_config_frozen
    is_config_frozen = True
