# standard library
import sys
import os
import asyncio
from typing import Any
from dataclasses import dataclass

# third-party
import psutil

# project
from truenas_api_conduit.constants import APP_NAME, SERVICENAME, LOCK_FILE, Endpoints
import truenas_api_conduit.core as core
from truenas_api_conduit.cli.cli_helpers import cli_print

__all__ = ["get_request_helper", "RequestHelper"]


@dataclass
class ServerConfig:
    address: str
    port: int
    request_header: str | None = None

    def __repr__(self) -> str:
        return f"ServerConfig(address={self.address}, port={self.port})"


@dataclass
class RawResponse:
    status: int
    text: str


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
        self, endpoint: Endpoints, json_dict: dict[str, Any] | None = None
    ) -> RawResponse:
        """no json = GET
        pass in json = POST"""

        if endpoint not in Endpoints:
            raise ValueError(f"Invalid endpoint: {endpoint}")

        cli_print.info("Making request")
        return asyncio.run(self._make_request(endpoint, json_dict))

    async def _make_request(
        self, endpoint: Endpoints, json_dict: dict[str, Any] | None
    ) -> RawResponse:
        import aiohttp
        import yaspin
        from yaspin.spinners import Spinners

        headers: dict[str, str] | None = (
            {APP_NAME: self.request_header} if self.request_header else None
        )

        try:
            with yaspin.yaspin(
                Spinners.bouncingBall,
                text="Sending request...",
                stream=sys.stderr,
            ):
                async with aiohttp.ClientSession() as session:
                    if json_dict is not None:
                        async with session.post(
                            f"http://{self.address}:{self.port}{endpoint}",
                            json=json_dict,
                            timeout=aiohttp.ClientTimeout(total=10),
                            headers=headers,
                        ) as response:
                            text = await response.text()
                            status = response.status
                    else:
                        async with session.get(
                            f"http://{self.address}:{self.port}{endpoint}",
                            timeout=aiohttp.ClientTimeout(total=10),
                            headers=headers,
                        ) as response:
                            text = await response.text()
                            status = response.status

        except aiohttp.ClientError as e:
            cli_print.error("Could not connect to TrueNAS API Conduit service: {e}".format(e=e))
            sys.exit(1)
        except Exception as e:
            cli_print.error("Unexpected error making request: {e}".format(e=e))
            sys.exit(1)

        return RawResponse(status=status, text=text)


def auto_find_server_config(
    lockfile_obj: core.Lockfile | None, lock_file_bad: bool = False
) -> ServerConfig | None:

    cli_print.debug("Auto-finding service port")

    service_proc: psutil.Process | None = None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
            # match against the executable name (index 0) and the first argument (index 1, the
            # script/module path) rather than the full joined cmdline, to avoid false positives
            # where SERVICENAME appears as a substring of an unrelated argument or path component.
            exe_and_script = cmdline[:2]
            if any(SERVICENAME in part for part in exe_and_script):
                service_proc = proc
                break
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue

    if service_proc:
        identifier = get_process_identifier(service_proc)
        cli_print.debug("Found service with identifier: {identifier}".format(identifier=identifier))

        if server_config := get_process_httpconfig(service_proc):
            if lock_file_bad:
                cli_print.warning(
                    "Lock file was bad or missing, yet the service is running. "
                    "That's not supposed to happen. Recommended to restart the service"
                )
            server_config.request_header = lockfile_obj.header if lockfile_obj else None
            cli_print.debug("Auto-found the server config: {server_config}".format(server_config=server_config))
            return server_config
        else:
            cli_print.error("The process is running, but does not have a port open")
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
        cli_print.warning("Could not get process port: {proc}".format(proc=proc))
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
        cli_print.warning("Could not signal service process with PID: {pid}".format(pid=pid))
        return False
    except Exception as e:
        cli_print.error("Unexpected error checking service status: {e}".format(e=e))
        return False


def check_service_status() -> ServerConfig | None:
    "If the service is up, return the port. If not, return None"

    # we check if we can pull everything from the lockfile first, its faster.
    if lockfile_obj := core.read_lockfile(LOCK_FILE):
        cli_print.debug("Found {lockfile_obj}".format(lockfile_obj=lockfile_obj))
        lockfile_address, lockfile_port = lockfile_obj.address.split(":")

        if proc := get_process_by_pid(lockfile_obj.pid):
            # if we find a process with this PID, we can't immediately trust it,
            # we gotta make sure the PID was not recycled

            cli_print.debug("Found process with PID: {lockfile_obj}".format(lockfile_obj=lockfile_obj.pid))

            # 1st check: do the address/port match the ones in the lockfile
            if server_config := get_process_httpconfig(proc):
                # server_address, server_port = server_config.address.split(":")

                cli_print.debug("Found {server_config}".format(server_config=server_config))
                if server_config.port == int(lockfile_port):
                    # If we look up the PID from the lockfile and the port on that process
                    # matches the port in the lockfile, it's definitely the right process.
                    cli_print.debug("Process port matches lockfile, must be the right process")
                    server_config.request_header = lockfile_obj.header
                    return server_config
                else:
                    cli_print.warning(
                        "Process port ({process_port}) does not match lockfile port ({lockfile_port})".format(process_port=server_config.port, lockfile_port=lockfile_port)
                    )
                    return auto_find_server_config(lockfile_obj)
            else:
                cli_print.warning("Could not get process port. Trying next check...")

            # 2nd check: does the process name match the app name
            if proc_ident := get_process_identifier(proc):  #      we got the name/cmdline
                cli_print.debug(
                    "Found process at PID {lockfile_pid} with identifier {proc_ident}".format(lockfile_pid=lockfile_obj.pid, proc_ident=proc_ident)
                )
                if SERVICENAME in proc_ident:
                    cli_print.debug("Name matches, checking signal")
                    # only return the lockfile port if we can confirm the process is alive
                    # via signal. If the signal fails, the PID is gone and the lockfile is stale.
                    if send_os_signal(lockfile_obj.pid):
                        return ServerConfig(
                            address=lockfile_address,
                            port=int(lockfile_port),
                            request_header=lockfile_obj.header,
                        )
                    else:
                        cli_print.warning(
                            "Found process with identifer {proc_ident} and PID {lockfile_pid}, "
                            "but could not signal it".format(proc_ident=proc_ident, lockfile_pid=lockfile_obj.pid)
                        )
                        return auto_find_server_config(lockfile_obj)
                else:
                    cli_print.warning(
                        "Found process with PID {lockfile_pid}, but its name ({proc_ident}) does not match. "
                        "This means the lock file is stale and the PID was recycled.".format(lockfile_pid=lockfile_obj.pid, proc_ident=proc_ident)
                    )
                    return auto_find_server_config(lockfile_obj, lock_file_bad=True)
            else:
                # HACK: process exists, but we can't get its name or http config
                # Might be fine, might not. This will probably vary depending on user
                # permissions. This is tricky cause if the above steps didn't work,
                # there's a good chance the auto-finder will fail too. So its best
                # to just return what the lockfile says and let the request fail.
                cli_print.info(
                    "Process identifier is not available for PID: {lockfile_pid}".format(lockfile_pid=lockfile_obj.pid)
                )
                return ServerConfig(
                    address=lockfile_address,
                    port=int(lockfile_port),
                    request_header=lockfile_obj.header,
                )
        else:
            # no service process found with this PID. Lock file must be stale
            cli_print.warning("Lock file is stale: PID {lockfile_pid} not found".format(lockfile_pid=lockfile_obj.pid))
            return auto_find_server_config(lockfile_obj, lock_file_bad=True)
    else:
        # if the lockfile doesn't exist or is malformed, use the auto-finder
        return auto_find_server_config(None, lock_file_bad=True)


def get_request_helper() -> RequestHelper | None:
    "if service is up, returns a RequestHelper. If not, returns None"

    if server_config := check_service_status():
        helper = RequestHelper(server_config)
        cli_print.debug("Initialized {helper}".format(helper=helper))
        return helper
