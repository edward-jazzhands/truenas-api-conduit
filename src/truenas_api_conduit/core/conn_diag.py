# standard library
import asyncio
import socket
import subprocess
from dataclasses import dataclass
from functools import partial
import logging
from typing import Callable, AsyncGenerator

from truenas_api_conduit.core import PLATFORM, Platform
from truenas_api_conduit.config import Config

log = logging.getLogger(__name__)

__all__ = ["run_connection_diagnostic", "ConnDiag"]


def simple_socket_check(address: str, port: int) -> bool:
    """Use address='8.8.8.8' and port=53 for a basic internet check."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect((address, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def use_ping_tool(address: str) -> bool:

    if PLATFORM == Platform.WINDOWS:
        args = ["ping", "-n", "1", "-w", "1000", address]
    else:
        args = ["ping", "-c", "1", "-W", "1", address]

    try:
        result = subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except Exception:
        return False


def curl_test(address: str, api_route: str, timeout: int = 2) -> bool | None:

    url = f"https://{address}{api_route}"

    log.debug("curl url: %s", url)

    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["curl", url, "--connect-timeout", f"{timeout}"],
            capture_output=True,
            text=True,
            timeout=timeout + 1,  # this shouldnt be necessary but just in case
        )
    except FileNotFoundError:
        log.debug("curl is not installed or not found on PATH.")
        return None
    except subprocess.CalledProcessError as e:
        log.debug(f"curl failed (exit {e.returncode}): {e.stderr.strip()}")
        if "SSL certificate problem" in e.stderr:
            return True
        else:
            return False
    except subprocess.TimeoutExpired:
        log.debug(f"curl timed out after {timeout} seconds")
        return False

    # NOTE: if we find "SSL certificate problem" it means the server is up
    # and we can reach it. This will be fine if validate_certs is set to False
    if "WebSocket" in result.stdout or "SSL certificate problem" in result.stderr:
        return True
    else:
        return False


@dataclass
class ConnDiag:
    has_internet: bool
    socket_check: bool
    ping_check: bool
    curl_check: bool | None

    def msg(self, value):
        if value is True:
            return "[bright_green]PASSED[default]"
        elif value is False:
            return "[bright_red]FAILED[default]"
        else:
            return "N/A"

    def __str__(self) -> str:
        return (
            "Connection diagnostics:\n"
            f"  Outbound internet:  {self.msg(self.has_internet)}\n"
            f"  TrueNAS port test:  {self.msg(self.socket_check)}\n"
            f"  TrueNAS ping test:  {self.msg(self.ping_check)}\n"
            f"  TrueNAS curl test:  {self.msg(self.curl_check)}\n"
        )


async def run_connection_diagnostic(
    config: Config,
) -> AsyncGenerator[tuple[str, bool | None], None]:
    """This is a generator so it can stream the test results back one at a time."""

    if ":" in config.truenas_host:
        host, port = config.truenas_host.split(":")
    else:
        # NOTE: The address for the TrueNAS websocket API is always the
        # same as the web UI's HTTPS address, with /api/current at the end.
        host = config.truenas_host
        port = 443

    diag_tests = {
        "has_internet": partial(simple_socket_check, "8.8.8.8", 53),
        "socket_check": partial(simple_socket_check, host, int(port)),
        "ping_check": partial(use_ping_tool, host),
        "curl_check": partial(curl_test, config.truenas_host, config.api_route),
    }

    loop = asyncio.get_running_loop()

    async def run_test(test_name: str, func: Callable) -> tuple[str, bool | None]:
        result = await loop.run_in_executor(None, func)
        return test_name, result

    tasks = [run_test(name, func) for name, func in diag_tests.items()]
    for future in asyncio.as_completed(tasks):
        test_name, result = await future
        yield test_name, result
