from typing import Final
from importlib.metadata import version
import tempfile
from pathlib import Path

APP_NAME: Final[str] = "truenas-api-conduit"

# Linux/Mac -> /tmp/my_app.lock
# Windows -> C:\Users\<user>\AppData\Local\Temp\my_app.lock
LOCK_FILE: Final[Path] = Path(tempfile.gettempdir()) / f"{APP_NAME}.lock"

# tempfile.gettempdir() checks TMPDIR, TEMP, and TMP env vars before falling back to
# platform defaults so it respects user/system overrides.

__version__: Final[str] = version(APP_NAME)
