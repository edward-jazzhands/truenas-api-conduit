"""
Linux service implementation using systemd user units.

No sudo required. Unit file is written to ~/.config/systemd/user/ and managed
entirely through `systemctl --user` and `loginctl`.
"""

# standard library
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

# local
from truenas_api_conduit import APP_NAME
from truenas_api_conduit.core import CONFIG_DIR
from truenas_api_conduit.service.base import BaseService

import logging
logger = logging.getLogger(__name__)


UNIT_NAME: str = f"{APP_NAME}.service"
SYSTEMD_USER_DIR: Path = Path.home() / ".config" / "systemd" / "user"
UNIT_PATH: Path = SYSTEMD_USER_DIR / UNIT_NAME


def _build_unit_file(executable: Path) -> str:
    """Build the systemd unit file content for the given executable path."""
    return textwrap.dedent(f"""\
        [Unit]
        Description={APP_NAME} background service
        After=network.target

        [Service]
        Type=simple
        ExecStart={executable}
        Restart=always
        RestartSec=5

        StandardOutput=journal
        StandardError=journal

        [Install]
        WantedBy=default.target
    """)


# <->-<-> Helpers <->-<->

def _resolve_daemon_executable() -> Path:
    """
    Resolve the absolute path to the headless daemon entry point.

    Looks for `truenas-api-conduitd` next to the current interpreter first
    (covers pipx/uv/venv installs), then falls back to PATH. Raises if not
    found — there is no sensible default.
    """
    # Same bin directory as the running interpreter (covers pipx / uv / venv)
    candidate = Path(sys.executable).parent / f"{APP_NAME}d"
    if candidate.is_file():
        return candidate.resolve()

    # Fall back to PATH
    found = shutil.which(f"{APP_NAME}d")
    if found:
        return Path(found).resolve()

    raise FileNotFoundError(
        f"Could not locate '{APP_NAME}d' daemon executable. "
        "Ensure the package is installed correctly."
    )


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    """Run a `systemctl --user` command and return the completed process."""
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
    )


def _require_systemctl(*args: str, error_context: str) -> None:
    """
    Run a `systemctl --user` command and raise RuntimeError on non-zero exit.
    """
    result = _systemctl(*args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{error_context}: {detail}")


# Service implementation 

class LinuxService(BaseService):

    def install(self) -> None:
        """
        Write the systemd user unit file and enable the service.

        Also calls `loginctl enable-linger` so the user unit survives logout
        and starts on boot without requiring an interactive session.
        """
        executable = _resolve_daemon_executable()
        logger.debug("Daemon executable resolved to: %s", executable)

        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

        unit_content = _build_unit_file(executable)
        UNIT_PATH.write_text(unit_content)
        logger.debug("Unit file written to: %s", UNIT_PATH)

        _require_systemctl("daemon-reload", error_context="daemon-reload failed")
        _require_systemctl("enable", UNIT_NAME, error_context="Failed to enable unit")


        # HACK: Path.home().name for the linger username is portable in most cases
        # but can be wrong on systems where the home directory name doesn't match the
        # login name (e.g. /home/jdoe for user john.doe). If you want to be precise,
        # pwd.getpwuid(os.getuid()).pw_name is the better way to do it.

        # Allow the user unit to run without an active login session.
        username = Path.home().name  # portable enough; avoids pwd import
        linger_result = subprocess.run(
            ["loginctl", "enable-linger", username],
            capture_output=True,
            text=True,
        )
        if linger_result.returncode != 0:
            # Non-fatal: warn and continue. The service will still work when the
            # user is logged in; it just won't auto-start on boot.
            logger.error(
                "loginctl enable-linger failed (service installed, but it will "
                "only start when you log in, not on boot): %s",
                (linger_result.stderr or linger_result.stdout).strip(),
            )

        logger.info("Service installed successfully.")

    def uninstall(self) -> None:
        """Stop, disable, and remove the unit file."""
        # Best-effort stop — ignore errors if already stopped.
        _systemctl("stop", UNIT_NAME)

        _require_systemctl("disable", UNIT_NAME, error_context="Failed to disable unit")

        if UNIT_PATH.exists():
            UNIT_PATH.unlink()
            logger.debug("Unit file removed: %s", UNIT_PATH)

        _require_systemctl("daemon-reload", error_context="daemon-reload failed")
        logger.info("Service uninstalled successfully.")

    def start(self, cfg: Config) -> None:
        _require_systemctl("start", UNIT_NAME, error_context="Failed to start service")
        logger.info("Service started.")

    def stop(self) -> None:
        _require_systemctl("stop", UNIT_NAME, error_context="Failed to stop service")
        logger.info("Service stopped.")

    def restart(self) -> None:
        _require_systemctl("restart", UNIT_NAME, error_context="Failed to restart service")
        logger.info("Service restarted.")

    def status(self) -> None:
        """
        Print live service status directly from systemctl.

        `systemctl --user status` produces its own Rich-style colourised output
        on a real TTY. We print it verbatim rather than trying to re-parse and
        re-render it — systemd's output is already the canonical status view.
        """
        result = _systemctl("status", UNIT_NAME)
        # status exits 0 (active), 3 (inactive/dead), or other codes for errors.
        # Print stdout regardless; it contains the useful human-readable block.
        output = result.stdout or result.stderr
        if output:
            print(output, end="")

    def __call__(self) -> None:
        """
        Headless entry point called by the daemon executable.

        The service infrastructure invokes this directly. Any daemon
        initialisation that must happen inside the service process (rather
        than at install time) goes here.
        """
        from truenas_api_conduit.core import ensure_config
        ensure_config()
        # Hand off to core business logic here.
        # e.g. from truenas_api_conduit.core import run; run()
        raise NotImplementedError("Wire up the core run loop here.")