"""
Linux service implementation using systemd.

Supports User, System, and Package install types.
"""

# standard library
import shutil
import subprocess
import sys
import os
from pathlib import Path
import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config

# local
from truenas_api_conduit import APP_NAME, SERVICENAME
import truenas_api_conduit.core as core
from truenas_api_conduit.service.base import BaseService, ServiceError
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


def build_unit_file(executable: Path) -> str:

    # systemd does not invoke a shell for ExecStart, so spaces must be escaped.
    executable_str = str(executable).replace(" ", r"\x20")

    return f"""\
[Unit]
Description={APP_NAME} background service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart={executable_str}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier={APP_NAME}
Environment=PYTHONUNBUFFERED=1
Environment=TRUENAS_APP_ENV=os_service

[Install]
WantedBy=default.target
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


def detect_existing_install() -> bool:
    "Detects if the service is already installed"

    # Priority: 1) User, 2) System, 3) Package
    # # HACK: WHat if there's more than one installed? This doesn't handle that

    # NOTE: user units should take precedence over system units.
    # System units take precedence over packaged units because local admin
    # configuration should take precedence over vendor defaults.

    if (SYSTEMD_USER_DIR / UNIT_NAME).exists():
        return True
    return False


# ? Common systemd/systemctl exit codes
# Code| Meaning ----------------- |Description -----------------
#   0 | Success/Active            |The program is running or service is OK.
#   1 | Generic/Unspecified Error |The program is dead and the `/var/run` pid file exists.
#   2 | Invalid Argument          |Invalid or excess arguments were passed to the command.
#   3 | Not Running               |The program is not running (stopped cleanly).
#   4 | Unknown Program State     |The program or service is not installed or unknown.
#  13 | Permission Denied         |The user does not have permission to perform the operation.
# 200 | Working Dir Missing       |Working directory is missing or inaccessible
# 203 | Executable Missing        |Executable is missing or inaccessible
# 217 | No such user              |The specified user does not exist


class LinuxService(BaseService):
    def __init__(self) -> None:
        self.installed: bool = detect_existing_install()
        self.unit_path: Path | None = SYSTEMD_USER_DIR / UNIT_NAME

        #! this is not used at the moment
        self.error_mapping = {
            1: "The service failed to start or stopped unexpectedly. Please check your system logs.",
            2: None,  # this means private/user does not need to know
            3: None,
            4: "Installation failed: The service configuration file could not be found.",
            13: "Administrative privileges are required. Please run this application as root/sudo.",
            200: "The service could not start because its working directory is missing or inaccessible.",
            203: "The service executable could not be found or executed. Verify the installation paths.",
            217: "The required system user account for this service does not exist.",
        }

        log.info("Initialized %s", self)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(installed={self.installed})"

    def _systemctl(
        self,
        *args: str,
        color: bool = False,
        required: bool = False,
        show_error_warning: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a systemctl command. Detects install type and appends --user if needed.
        If required, raise ServiceError on non-zero exit."""

        # NOTE: systemctl checks $PAGER and $TERM, not just whether stdout is
        # a TTY. Even with captured stdout, it can invoke a pager if those env vars
        # are set (e.g. PAGER=less in the user's shell
        cmd: list[str] = ["systemctl", "--no-pager", "--user", *args]

        # NOTE: passing os.environ: for --user mode, systemctl communicates
        # with the user's D-Bus session via DBUS_SESSION_BUS_ADDRESS (and
        # XDG_RUNTIME_DIR). Without the ambient environment those vars are absent
        # and the --user call fails with "Failed to connect to bus: No such file
        # or directory". So passing the full environment is necessary here

        # NOTE: SYSTEMD_COLORS=1 is needed to force systemctl to use colors even tho
        # it will normally disable them when stdout is not a TTY.

        env: dict[str, str] = {**os.environ, "SYSTEMD_COLORS": "1" if color else "0"}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)

        log.debug("%s command returned code: %d", *args, result.returncode)

        if result.returncode != 0:

            # If required, raise an error
            # If not required, log a warning and continue
            # If not required and no warning needed, make a debug log
            if required:
                err_string = (
                    f" Command failed. (Code {result.returncode})\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Output:\n"
                    f"{result.stderr}"
                )
                if mapping := self.error_mapping.get(result.returncode):
                    err_string += f"\n{mapping}"
                raise ServiceError(err_string)
            else:
                if show_error_warning:
                    log.warning(
                        "%s command failed: %s",
                        *args,
                        (result.stderr or result.stdout).strip(),
                    )
                else:
                    log.debug(
                        "%s command failed (ignored): %s",
                        *args,
                        (result.stderr or result.stdout).strip(),
                    )

        return result

    def install(self) -> None:
        # Writes the systemd unit file and enables the service.
        # Also calls `loginctl enable-linger` (if user install) so the user unit
        # survives logout and starts on boot without requiring an interactive session.

        # NOTE: This entire function will be wrappped by a try/except block
        # by the CLI when it runs it, so anything we don't catch here will
        # be caught by the CLI. Same with all the other public methods in this class.

        # If there's already an existing install then this can just overwrite it
        # for now. This should maybe be improved in the future.
        console_stdout.print("Starting installer")

        executable = resolve_daemon_executable()
        console_stdout.print(f"Daemon executable resolved to: {executable}")

        # TODO: This entire install should be done in a single atomic operation,
        # so that it doesn't leave any leftover files or new directories
        # if the install fails for any reason.

        systemd_unit_dir = SYSTEMD_USER_DIR
        try:
            systemd_unit_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            err_string = core.examine_os_error(e)
            log.error("Failed to create systemd unit directory: %s", err_string)
            raise
        console_stdout.print("Created systemd unit directory: %s", systemd_unit_dir)

        self.unit_path = systemd_unit_dir / UNIT_NAME
        unit_content = build_unit_file(executable)
        console_stdout.print("Unit path: %s", self.unit_path)

        try:
            self.unit_path.write_text(unit_content, encoding="utf-8")
        except OSError as e:
            err_string = core.examine_os_error(e)
            log.error("Failed to write systemd unit file: %s", err_string)
            raise
        console_stdout.print("Unit file written to: %s", self.unit_path)

        # NOTE: The commands we will get here are:
        # - systemctl [--user] daemon-reload
        # - systemctl [--user] enable <path-to-unit-file>

        self._systemctl("daemon-reload", required=True)

        # NOTE: Install passes the full unit file path instead of just UNIT_NAME.
        # Enabling by name requires the unit to have already been installed.
        # For first installs you have to always pass the full path
        self._systemctl("enable", str(self.unit_path), required=True)
        console_stdout.print(f"Enabled systemd unit: {self.unit_path}")
        console_stdout.print("Service installed successfully, ready to start.")

    def uninstall(self) -> None:
        # Stop, disable, and remove the unit file.

        if not self.installed:
            console_stdout.print("No service installation detected.")
            return

        result1 = self._systemctl("stop", UNIT_NAME, show_error_warning=False)
        if result1.returncode == 3:
            # this means the service is already stopped, so we can ignore the error
            log.debug("Service already stopped, ignoring error")
        elif result1.returncode != 0:
            log.warning(
                "Failed to stop the service (exit code %d): %s",
                result1.returncode,
                (result1.stderr or result1.stdout).strip(),
            )
            # It should be possible to ignore this error and just plough through

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
                err_string = core.examine_os_error(e)
                log.error("Failed to remove unit file: %s", err_string)
                raise

        self._systemctl("daemon-reload", required=True)

        # clear the unit's failure state, ensuring that systemd
        # forgets about the unit completely after removal.
        self._systemctl("reset-failed", UNIT_NAME)

        # Reset class state for future proofing - this protects against nothing at all at
        # the moment but if I ever re-use this class in the future for something long
        # running, I'll want this to be here.
        self.installed = False
        self.unit_path = None

    def start(self, cfg: Config) -> None:
        # TODO: The config is not used here yet, but it should be in order to
        # pass in CLI options that the user may have set (--truenas-host and
        # --api-key) to the service.
        # This might require writing them out to a temp file or something, since
        # the service has to start by itself and can't use the stdin startup

        # `systemctl [--user] start truenas-api-conduit`
        self._systemctl("start", UNIT_NAME, required=True)

    def stop(self) -> None:
        # `systemctl [--user] stop truenas-api-conduit`

        self._systemctl("stop", UNIT_NAME, required=True)

    # systemd Restart vs Reload
    # Restart: kills the process and starts a new one with a new pid

    # Reload: sends a signal (usually SIGHUP) to the currently running process, telling
    # it to re-read its configuration files without actually shutting down. Unless you
    # have explicitly written signal handlers in your main daemon code to catch SIGHUP
    # and hot-reload configs, you want restart.

    def restart(self) -> None:
        # `systemctl [--user] restart truenas-api-conduit`

        self._systemctl("restart", UNIT_NAME, required=True)

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

    def detect_service(self) -> core.AppEnv:
        # This exists to detect how the service is running. It's used by the
        # CLI to determine how to send start/stop/reset commands to the service.

        # First lets see if the lockfile exists.
        if lock_dict := core.read_lockfile():
            log.debug(
                "Found lockfile with:\n" "PID: %s\nAddress: %s\nPort: %s",
                lock_dict["pid"],
                lock_dict["address"],
                lock_dict["socket_port"],
                lock_dict["app_env"],
            )
            # This would raise an error if the app_env is invalid, but I
            # control the lock file so I can guarantee it's valid.
            return core.AppEnv(lock_dict["app_env"])

        # If we don't find a lockfile then move onto other methods...

        status = self.status(stdout=False)
        # 0 or 3 would indicate its a systemd installed service
        # 0 = running (there should be a lockfile if that's the case)
        # 3 = stopped
        if status in (0, 3):
            return core.AppEnv.OS_SERVICE

        #! I believe anything else would indicate its not installed as a service,
        # or just not installed in general. But im not 100% sure.
        else:
            # FIXME: improve this logic this is not very robust.
            return core.AppEnv.STANDALONE

        # NOTE: Docker should be irrelevant for this function
