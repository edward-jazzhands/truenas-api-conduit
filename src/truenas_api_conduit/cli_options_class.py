from dataclasses import dataclass


@dataclass
class CLIOptions:
    """dataclass\n
    ```
    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    """

    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
