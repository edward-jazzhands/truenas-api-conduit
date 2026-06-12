"""
Linux service implementation using systemd.

Supports User, System, and Package install types.
"""

# standard library
import shutil
import subprocess
import sys
import pwd
import os
from pathlib import Path
import logging
from typing import TYPE_CHECKING, assert_never, Final

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

# local
from truenas_api_conduit import APP_NAME, SERVICENAME, InstallType
from truenas_api_conduit.core import examine_os_error
from truenas_api_conduit.service.base import BaseService
from truenas_api_conduit.console import console_stdout  # , console_stderr

# NOTE: log messages are configured to go to stderr
log = logging.getLogger(__name__)

UNIT_NAME: Final[str] = f"{APP_NAME}.service"
SYSTEMD_USER_DIR: Final[Path] = (
    Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    / "systemd"
    / "user"
)

__all__ = [
    "LinuxService",
]


def build_unit_file(executable: Path, install_type: InstallType) -> str:

    # systemd does not invoke a shell for ExecStart, so spaces must be escaped.
    executable_str = str(executable).replace(" ", r"\x20")

    wanted_by = (
        "default.target" if install_type == InstallType.USER else "multi-user.target"
    )

    # Sandboxing directives like ProtectHome can fail or break --user instances
    # because user services ypically need access to the user's home directory.
    # We only apply these to system/package level installs.
    if install_type in (InstallType.SYSTEM, InstallType.PACKAGE):
        security_block = (
            "# These 4 security directives are only for system/package level installs:\n"
            "NoNewPrivileges=true\n"
            "PrivateTmp=true\n"
            "ProtectSystem=strict\n"
            "ProtectHome=read-only"
        )
    else:
        security_block = ""

    return f"""\
[Unit]
Description={APP_NAME} background service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart={executable_str}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier={APP_NAME}
Environment=PYTHONUNBUFFERED=1
{security_block}
[Install]
WantedBy={wanted_by}
"""


def resolve_daemon_executable() -> Path:
    "Resolve the absolute path to the headless daemon entry point."

    # Looks for `truenas-api-conduitd` next to the current interpreter first
    # (covers pipx/uv/venv installs), then falls back to PATH. Raises if not
    # found, there is no sensible default.

    # Same bin directory as the running interpreter (covers pipx / uv / venv)
    candidate = Path(sys.executable).parent / SERVICENAME
    if candidate.is_file():
        return candidate.resolve()

    # Fall back to PATH
    found = shutil.which(SERVICENAME)
    if found:
        return Path(found).resolve()

    raise FileNotFoundError(
        f"Could not locate '{SERVICENAME}' daemon executable. "
        "Ensure the package is installed correctly."
    )


def detect_existing_install() -> InstallType | None:
    "Detects if the service is already installed and returns the install type."

    # Priority: 1) User, 2) System, 3) Package
    # # HACK: WHat if there's more than one installed? This doesn't handle that

    # NOTE: user units should take precedence over system units.
    # System units take precedence over packaged units because local admin
    # configuration should take precedence over vendor defaults.

    if (SYSTEMD_USER_DIR / UNIT_NAME).exists():
        return InstallType.USER
    if (Path("/etc/systemd/system") / UNIT_NAME).exists():
        return InstallType.SYSTEM
    if (Path("/usr/lib/systemd/system") / UNIT_NAME).exists():
        return InstallType.PACKAGE
    return None


def get_systemd_unit_dir(install_type: InstallType) -> Path:
    "Returns the systemd unit directory for the given install type."

    match install_type:
        case InstallType.SYSTEM:
            return Path("/etc/systemd/system")
        case InstallType.USER:
            return SYSTEMD_USER_DIR
        case InstallType.PACKAGE:
            return Path("/usr/lib/systemd/system")
            #! FIXME: Package install should be handled automatically by the package
            # manager, we're not supposed to manually copy stuff into this directory.
            # The package manager will handle this for us so I'm not sure it will
            # even need to use this function at all in that mode. But this will
            # stay here until I'm sure.
        case _:
            assert_never(install_type)


class LinuxService(BaseService):

    def __init__(self) -> None:
        self.install_type: InstallType | None = detect_existing_install()
        # Only set unit_path if we know the install type
        self.unit_path: Path | None = (
            get_systemd_unit_dir(self.install_type) / UNIT_NAME
            if self.install_type
            else None
        )
        log.info("Initialized %s", self)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(install_type={self.install_type!r})"

    def _systemctl(
        self,
        *args: str,
        color: bool = False,
        show_error_warning: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        # Run a `systemctl` command. Dynamically appends --user if needed.
        # If require, raise RuntimeError on non-zero exit."

        # NOTE: systemctl checks $PAGER and $TERM, not just whether stdout is
        # a TTY. Even with captured stdout, it can invoke a pager if those env vars
        # are set (e.g. PAGER=less in the user's shell
        base_cmd: list[str] = ["systemctl", "--no-pager"]

        match self.install_type:
            case InstallType.USER:
                cmd = [*base_cmd, "--user", *args]
            case InstallType.SYSTEM | InstallType.PACKAGE:
                cmd = [*base_cmd, *args]
            case None:
                raise ValueError("No install type detected")
            case _:
                assert_never(self.install_type)

        # NOTE: passing os.environ: for --user mode, systemctl communicates
        # with the user's D-Bus session via DBUS_SESSION_BUS_ADDRESS (and
        # XDG_RUNTIME_DIR). Without the ambient environment those vars are absent
        # and the --user call fails with "Failed to connect to bus: No such file
        # or directory". So passing the full environment is necessary here

        # SYSTEMD_COLORS=1 is needed to force systemctl to use colors even tho
        # it will normally disable them when stdout is not a TTY.
        env: dict[str, str] = {**os.environ, "SYSTEMD_COLORS": "1" if color else "0"}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)

        log.debug("%s command returned code: %d", *args, result.returncode)
        if result.returncode != 0:
            if show_error_warning:
                log.warning(
                    "%s command failed: %s",
                    *args,
                    (result.stderr + result.stdout).strip()
                )
            else:
                log.debug(
                    "%s command failed (ignored): %s",
                    *args,
                    (result.stderr + result.stdout).strip()
                )


        return result

    def install(self, install_type: InstallType) -> None:
        # Writes the systemd unit file and enables the service.
        # Also calls `loginctl enable-linger` (if user install) so the user unit survives logout
        # and starts on boot without requiring an interactive session.

        # NOTE: This entire function will be wrappped by a try/except block
        # by the CLI when it runs it, so anything we don't catch here will
        # be caught by the CLI. Same with all the other public methods in this class.

        # If there's already an existing install then this can just overwrite it
        # for now. This should maybe be improved in the future.
        console_stdout.print(f"Starting installer type={install_type}")
        self.install_type = install_type

        if (
            self.install_type in (InstallType.SYSTEM, InstallType.PACKAGE)
            and os.geteuid() != 0
        ):
            raise PermissionError(
                "System-level installations require root privileges (run with sudo)."
            )

        executable = resolve_daemon_executable()
        console_stdout.print(f"Daemon executable resolved to: {executable}")

        # TODO: This entire install should be done in a single atomic operation,
        # so that it doesn't leave any leftover files or new directories
        # if the install fails for any reason.

        systemd_unit_dir = get_systemd_unit_dir(self.install_type)
        try:
            systemd_unit_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            err_string = examine_os_error(e)
            log.error("Failed to create systemd unit directory: %s", err_string)
            raise
        console_stdout.print("Created systemd unit directory: %s", systemd_unit_dir)

        self.unit_path = systemd_unit_dir / UNIT_NAME
        unit_content = build_unit_file(executable, self.install_type)
        console_stdout.print("Unit path: %s", self.unit_path)

        try:
            self.unit_path.write_text(unit_content, encoding="utf-8")
        except OSError as e:
            err_string = examine_os_error(e)
            log.error("Failed to write systemd unit file: %s", err_string)
            raise
        console_stdout.print("Unit file written to: %s", self.unit_path)

        # NOTE: The commands we will get here are:
        #  - systemctl [--user] daemon-reload
        #  - systemctl [--user] enable <path-to-unit-file>

        #! LAST THOUGHT: I need to go through these systemctl commands and
        # validate which ones are actually required and will necessitate
        # stopping the function if they fail.
        
        try:
            self._systemctl(
                "daemon-reload", show_error_warning=True
            )
        
            # NOTE: Install passes the full unit file path instead of just UNIT_NAME.
            # Enabling by name requires the unit to have already been installed.
            # For first installs you have to always pass the full path 
            self._systemctl(
                "enable",
                str(self.unit_path),
                show_error_warning=True
            )
        except Exception as e:
            log.error("Failed to enable systemd unit: %s", e)
            raise  # hoist it to CLI
        console_stdout.print(f"Enabled systemd unit: {self.unit_path}")

        if self.install_type == InstallType.USER:
            # this will allow the user unit to run without an active login session
            username = pwd.getpwuid(os.getuid()).pw_name
            result = subprocess.run(
                ["loginctl", "enable-linger", username],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console_stdout.print(
                    "Enabled 'linger' for user service. That's systemd's fancy way "
                    "of saying the service will start on boot without user login."
                )
            else:
                # Non-fatal: warn and continue. The service will still work when the
                # user is logged in, it just won't auto-start on boot.
                log.error(
                    "loginctl enable-linger failed (service installed, but it will "
                    "only start when you log in, not on boot)"
                )                

        console_stdout.print("Service installed successfully, ready to start.")

    def uninstall(self) -> None:
        # Stop, disable, and remove the unit file.

        if not self.install_type:
            console_stdout.print("No service installation detected.")
            return

        # Best-effort stop, ignore errors if already stopped.
        self._systemctl("stop", UNIT_NAME)

        # disable can fail if the unit is already disabled or
        # partially removed, so we treat it as best-effort.
        self._systemctl("disable", UNIT_NAME)

        # NOTE: running `systemctl disable`` only removes the symlinks that systemd created
        # in directories like default.target.wants/ to start the service on boot. It
        # leaves the .service file alone, so we have to delete it manually.
        if self.unit_path and self.unit_path.exists():
            try:
                self.unit_path.unlink(missing_ok=True)
                log.info("Unit file removed: %s", self.unit_path)
            except OSError as e:
                err_string = examine_os_error(e)
                log.error("Failed to remove unit file: %s", err_string)
                raise

        self._systemctl(
            "daemon-reload", show_error_warning=True
        )

        # clear the unit's failure state, ensuring that systemd
        # forgets about the unit completely after removal.
        self._systemctl("reset-failed", UNIT_NAME)

        # Reset class state for future proofing - this protects against nothing at all at
        # the moment but if I ever re-use this class in the future for something long
        # running, I'll want this to be here.
        self.install_type = None
        self.unit_path = None
        console_stdout.print("Service uninstalled successfully.")

    def start(self, cfg: Config) -> None:
        # TODO: The config is not used here yet, but it should be in order to
        # pass in CLI options that the user may have set (--truenas-host and 
        # --api-key) to the service.
        # This might require writing them out to a temp file or something, since
        # the service has to start by itself and can't use the stdin startup

        #! TODO: These should return the result and exit code back to
        # the CLI maybe instead of just printing/showing it here? Cleaner
        # separation of concerns.

        # `systemctl [--user] start truenas-api-conduit`
        result = self._systemctl(
            "start", UNIT_NAME, show_error_warning=True
        )

    def stop(self) -> None:
        # `systemctl [--user] stop truenas-api-conduit`
        result = self._systemctl(
            "stop", UNIT_NAME, show_error_warning=True
        )

    # systemd Restart vs Reload
    # Restart: kills the process and starts a new one with a new pid

    # Reload: sends a signal (usually SIGHUP) to the currently running process, telling
    # it to re-read its configuration files without actually shutting down. Unless you
    # have explicitly written signal handlers in your main daemon code to catch SIGHUP
    # and hot-reload configs, you want restart.

    def restart(self) -> None:
        # `systemctl [--user] restart truenas-api-conduit`
        # NOTE: 'restart' kills and restarts the process. 'reload' sends a SIGHUP to
        # re-read configs without dying.
        result = self._systemctl(
            "restart", UNIT_NAME, show_error_warning=True
        )
        log.info("Service restarted.")

    def status(self, stdout: bool = True) -> int:
        # Print live service status directly from systemctl.

        # NOTE: `systemctl status` produces its own Rich-style colourised output
        # on a real TTY. We print it verbatim rather than trying to re-parse and
        # re-render it. systemd's output is already the canonical status view.

        result = self._systemctl("status", UNIT_NAME, color=True)

        # status exits 0 (active), 3 (inactive/dead), or other codes for errors.
        # Print stdout regardless; it contains the useful human-readable block.
        output = result.stdout or result.stderr

        if output and stdout:
            console_stdout.print(output, end="")
            return result.returncode
        else:  # if there's no output then who tf knows what's goin on, but it ain't success.
            log.error(
                "systemctl status produced no output (exit code %d)", result.returncode
            )
            return 1