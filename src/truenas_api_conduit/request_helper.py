# standard library
import sys
import logging
import os
from typing import Any, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    import requests

# third-party
import psutil

# project
from truenas_api_conduit import APP_NAME
import truenas_api_conduit.core as core

log = logging.getLogger(__name__)

__all__ = ["get_request_helper", "RequestHelper"]

SERVICE_NAME = APP_NAME + "d"


@dataclass
class ServerConfig:
    address: str
    port: int
    request_header: str | None = None


class RequestHelper:

    def __init__(
        self,
        server_config: ServerConfig,
    ) -> None:
        self.address = server_config.address
        self.port = server_config.port
        self.request_header = server_config.request_header

    def __repr__(self) -> str:
        return f"RequestHelper(address={self.address}, port={self.port})"

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

        if self.request_header:
            headers = {APP_NAME: self.request_header}
        else:
            headers = None

        try:
            with yaspin.yaspin(
                Spinners.bouncingBall,
                text="Sending request...",
                stream=sys.stderr,
            ):
                if json_dict is not None:
                    response = requests.post(
                        f"http://{self.address}:{self.port}{endpoint}",
                        json=json_dict,
                        timeout=10,
                        headers=headers,
                    )
                else:
                    response = requests.get(
                        f"http://{self.address}:{self.port}{endpoint}",
                        timeout=10,
                        headers=headers,
                    )

        except requests.exceptions.RequestException as e:
            log.error("Could not connect to TrueNAS API Conduit service: %s", e)
            sys.exit(1)
        except Exception as e:
            log.error("Unexpected error making request: %s", e)
            sys.exit(1)

        return response


def auto_find_server_config(
    lock_dict: dict[str, Any] | None, lock_file_bad: bool = False
) -> ServerConfig | None:

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

        if server_config := get_process_httpconfig(service_proc):
            if lock_file_bad:
                log.warning(
                    "Lock file was bad or missing, yet the service is running. "
                    "That's not supposed to happen. Recommended to restart the service"
                )
            server_config.request_header = lock_dict["header"] if lock_dict else None
            log.debug("Auto-found the server config: %s", server_config)
            return server_config
        else:
            log.critical("The process is running, but does not have a port open")
            return None
    else:
        return None  # This tells us the process is definitely not running


def get_process_by_pid(pid: int) -> psutil.Process | None:

    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return None


def get_process_httpconfig(proc: psutil.Process) -> ServerConfig | None:

    try:
        # filter out UNIX sockets or UDP
        connections = proc.net_connections(kind="tcp")
        for conn in connections:
            if conn.status == psutil.CONN_LISTEN:
                return ServerConfig(address=conn.laddr.ip, port=conn.laddr.port)
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


def check_service_status() -> ServerConfig | None:
    "If the service is up, return the port. If not, return None"

    # we check if we can pull everything from the lockfile first, its faster.
    if lock_dict := core.read_lockfile():
        log.debug("Found lockfile with PID: %s", lock_dict["pid"])

        if proc := get_process_by_pid(lock_dict["pid"]):
            # if we find a process with this PID, we can't immediately trust it,
            # we gotta make sure the PID was not recycled

            log.debug("Found process with PID: %s", lock_dict["pid"])

            # 1st check: do the address/port match the ones in the lockfile
            if server_config := get_process_httpconfig(proc):
                log.debug(
                    "Found http config: %s:%s", server_config.address, server_config.port
                )
                if server_config.port == lock_dict["socket_port"]:
                    # If we look up the PID from the lockfile and the port on that process
                    # matches the port in the lockfile, it's definitely the right process.
                    log.debug("Process port matches lockfile, must be the right process")
                    server_config.request_header = lock_dict["header"]
                    return server_config
                else:
                    log.warning(
                        "Process port (%s) does not match lockfile port (%s)",
                        server_config.port,
                        lock_dict["socket_port"],
                    )
                    return auto_find_server_config(lock_dict)
            else:
                log.warning("Could not get process port. Trying next check...")

            # 2nd check: does the process name match the app name
            if proc_ident := get_process_identifier(proc):  #      we got the name/cmdline
                log.debug(
                    "Found process at PID %s with identifier %s",
                    lock_dict["pid"],
                    proc_ident,
                )
                if SERVICE_NAME in proc_ident:
                    log.debug("Name matches, checking signal")
                    # only return the lockfile port if we can confirm the process is alive
                    # via signal. If the signal fails, the PID is gone and the lockfile is stale.
                    if send_os_signal(lock_dict["pid"]):
                        return ServerConfig(
                            address=lock_dict["address"],
                            port=lock_dict["socket_port"],
                            request_header=lock_dict["header"],
                        )
                    else:
                        log.warning(
                            "Found process with identifer %s and PID %s, "
                            "but could not signal it",
                            proc_ident,
                            lock_dict["pid"],
                        )
                        return auto_find_server_config(lock_dict)
                else:
                    log.warning(
                        "Found process with PID %s, but its name (%s) does not match. "
                        "This means the lock file is stale and the PID was recycled.",
                        lock_dict["pid"],
                        proc_ident,
                    )
                    return auto_find_server_config(lock_dict, lock_file_bad=True)
            else:
                # HACK: process exists, but we can't get its name or http config
                # Might be fine, might not. This will probably vary depending on user
                # permissions. This is tricky cause if the above steps didn't work,
                # there's a good chance the auto-finder will fail too. So its best
                # to just return what the lockfile says and let the request fail.
                log.info(
                    "Process identifier is not available for PID: %s", lock_dict["pid"]
                )
                return ServerConfig(
                    address=lock_dict["address"],
                    port=lock_dict["socket_port"],
                    request_header=lock_dict["header"],
                )
        else:
            # no service process found with this PID. Lock file must be stale
            log.warning("Lock file is stale: PID %s not found", lock_dict["pid"])
            return auto_find_server_config(lock_dict, lock_file_bad=True)
    else:
        # if the lockfile doesn't exist or is malformed, use the auto-finder
        return auto_find_server_config(None, lock_file_bad=True)


def get_request_helper() -> RequestHelper | None:
    "if service is up, returns a RequestHelper. If not, returns None"

    if server_config := check_service_status():
        log.debug("Server config: %s", server_config)
        return RequestHelper(server_config)
