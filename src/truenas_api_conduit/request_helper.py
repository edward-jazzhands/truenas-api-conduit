# standard library
import sys
import logging
import os
import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import requests

# third-party
import psutil

# project
from truenas_api_conduit import APP_NAME, LOCK_FILE
import truenas_api_conduit.core as core

log = logging.getLogger(__name__)

__all__ = ["get_request_helper", "RequestHelper"]

SERVICE_NAME = APP_NAME + "d"


class RequestHelper:

    def __init__(
        self,
        port: int,
    ) -> None:
        self.port = port

    def __call__(
        self, endpoint: core.Endpoints, json_dict: dict[str, Any] | None = None
    ) -> requests.Response:
        """no json = GET
        pass in json = POST"""

        if endpoint not in core.Endpoints:
            raise ValueError(f"Invalid endpoint: {endpoint}")

        log.info("Making request")

        import requests
        import yaspin
        from yaspin.spinners import Spinners

        try:
            with yaspin.yaspin(
                Spinners.bouncingBall,
                text="Sending request...",
                stream=sys.stderr,
            ):
                if json_dict is not None:
                    response = requests.post(
                        f"http://127.0.0.1:{self.port}{endpoint}",
                        json=json_dict,
                        timeout=10,  # ~ Added a timeout to prevent the CLI from hanging indefinitely
                    )
                else:
                    response = requests.get(
                        f"http://127.0.0.1:{self.port}{endpoint}",
                        timeout=10,  # ~ Added a timeout here as well
                    )
        # ~ Broadened the exception catch to handle timeouts and generic request errors
        except requests.exceptions.RequestException as e:
            log.error("Could not connect to TrueNAS API Conduit service: %s", e)
            sys.exit(1)
        except Exception as e:
            log.error("Unexpected error making request: %s", e)
            sys.exit(1)

        return response


def auto_find_service_port() -> int | None:

    log.debug("Auto-finding service port")

    service_proc: psutil.Process | None = None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
            # match against the executable name (index 0) and the first argument (index 1, the
            # script/module path) rather than the full joined cmdline, to avoid false positives
            # where SERVICE_NAME appears as a substring of an unrelated argument or path component.
            exe_and_script = cmdline[:2]
            if any(SERVICE_NAME in part for part in exe_and_script):
                service_proc = proc
                break
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue

    if service_proc:
        identifier = get_process_identifier(service_proc)
        log.debug("Found service with identifier: %s", identifier)

        if port := get_process_port(service_proc):
            log.debug("Auto-found the service port: %s", port)
            return port
        else:
            log.critical("The process is running, but does not have a port open")
            return None
    else:
        return None  # ~ This tells us the process is definitely not running


def get_process_by_pid(pid: int) -> psutil.Process | None:

    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return None


def get_process_port(proc: psutil.Process) -> int | None:

    try:
        # filter out UNIX sockets or UDP
        connections = proc.net_connections(kind="tcp")
        for conn in connections:
            if conn.status == psutil.CONN_LISTEN:
                return conn.laddr.port
    except psutil.NoSuchProcess, psutil.AccessDenied:
        log.warning("Could not get process port: %s", proc)
        return None

    return None


def get_process_identifier(proc: psutil.Process) -> str | None:

    try:
        # NOTE: you can't trust proc.name(), proc.cmdline() is what you want to check
        cmdline = proc.cmdline()
        if cmdline:
            return " ".join(cmdline)
        return proc.name()
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return None


def send_os_signal(pid: int, sig: int = 0) -> bool:

    try:
        os.kill(pid, sig)
        return True
    except PermissionError:
        # process exists, but we can't signal it, which is fine. probably
        # just because its owned by root or some other user. Should still work.
        return True
    except ProcessLookupError:
        log.warning("Could not signal service process with PID: %s", pid)
        return False
    except Exception as e:
        log.error("Unexpected error checking service status: %s", e)
        return False


def read_lockfile() -> dict[str, Any] | None:

    try:
        with open(LOCK_FILE, "r") as f:
            lock_dict = json.loads(f.read())
        assert isinstance(lock_dict, dict)
        assert isinstance(lock_dict["pid"], int)
        assert isinstance(lock_dict["socket_port"], int)
        return lock_dict
    except FileNotFoundError:
        log.info("Did not find a lock file")
        return
    except (json.JSONDecodeError, AssertionError, KeyError) as e:
        log.error("Malformed lock file: %s", e)
        return
    except Exception as e:
        log.error("Unexpected error reading lock file: %s", e)
        return


def check_service_status() -> int | None:
    "If the service is up, return the port. If not, return None"

    # we check if we can pull everything from the lockfile first, its faster.
    if lock_dict := read_lockfile():
        log.debug("Found lockfile with PID: %s", lock_dict["pid"])

        if proc := get_process_by_pid(lock_dict["pid"]):
            # if we find a process with this PID, we can't immediately trust it,
            # we gotta make sure the PID was not recycled

            log.debug("Found process with PID: %s", lock_dict["pid"])

            # 1st check: does the port match the one in the lockfile
            if proc_port := get_process_port(proc):
                log.debug("Found process port: %s", proc_port)
                if proc_port == lock_dict["socket_port"]:
                    # If we look up the PID from the lockfile and the port on that process
                    # matches the port in the lockfile, it's definitely the right process.
                    log.debug("Process port matches lockfile, must be the right process")
                    return proc_port
                else:
                    log.warning(
                        "Process port (%s) does not match lockfile port (%s)",
                        proc_port,
                        lock_dict["socket_port"],
                    )
                    return auto_find_service_port()
            else:
                log.warning("Could not get process port. Trying next check...")

            # 2nd check: does the process name match the app name
            if proc_ident := get_process_identifier(proc):  #      we got the name/cmdline
                log.debug(
                    "Found process at PID %s with identifier %s",
                    lock_dict["pid"],
                    proc_ident,
                )
                if (
                    SERVICE_NAME in proc_ident
                ):  #                ...and the identifier matches
                    log.debug("Name matches, checking signal")
                    # only return the lockfile port if we can confirm the process is alive
                    # via signal. If the signal fails, the PID is gone and the lockfile is stale.
                    if send_os_signal(lock_dict["pid"]):
                        return lock_dict["socket_port"]
                    else:
                        log.warning(
                            "Found process with identifer %s and PID %s, "
                            "but could not signal it",
                            proc_ident,
                            lock_dict["pid"],
                        )
                        return auto_find_service_port()
                else:
                    log.warning(
                        "Found process with PID %s, but its name (%s) does not match. "
                        "This means the lock file is stale and the PID was recycled.",
                        lock_dict["pid"],
                        proc_ident,
                    )
                    return auto_find_service_port()
            else:
                # process exists, but we can't get its name. Might be fine, might not.
                # this will probably vary depending on user permissions. This is tricky
                # cause it might be ok, so we don't want to trigger the full auto-find yet.
                log.info(
                    "Process identifier is not available for PID: %s", lock_dict["pid"]
                )
                return lock_dict["socket_port"]
        else:
            # no service process found with this PID. Lock file must be stale
            log.warning("Lock file is stale: PID %s not found", lock_dict["pid"])
            return auto_find_service_port()
    else:
        # if the lockfile doesn't exist or is malformed, use the auto-finder
        return auto_find_service_port()


def get_request_helper() -> RequestHelper | None:
    "if service is up, returns a RequestHelper. If not, returns None"

    if service_port := check_service_status():
        return RequestHelper(service_port)
