# standard library
import json
import os
import logging
import asyncio
import sys
from enum import Enum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    # ws_client contains the import for the websockets library so we gain
    # a little bit by making it a lazy import when its needed.
    from truenas_api_conduit.core.ws_client import TrueNASClient
    from truenas_api_conduit.core.__main__ import Unlocker
    from truenas_api_conduit.config import Config, AppBaseConfig

# third party
from aiohttp import web

# from aiohttp.web_runner import GracefulExit

# project
from truenas_api_conduit import APP_NAME, SERVICENAME
from truenas_api_conduit.core import examine_os_error
import truenas_api_conduit.log_setup as log_setup

log_setup.init_logging()
log = logging.getLogger(__name__)


class RequestHeader(Enum):
    NONE = 0
    MISSING = 1
    INCORRECT = 2
    CORRECT = 3


STEALTH_RESPONSE: Final[web.Response] = web.Response(
    text="<html><body>404 Not Found</body></html>", content_type="text/html", status=404
)

# <><><><> HELPERS <><><><>


def check_request_header(request: web.Request) -> None | web.Response:
    """None = CORRECT | web.Response = INCORRECT"""

    cfg: Config | AppBaseConfig = request.app["config"]
    looking_for: str | None = cfg.request_header

    if looking_for is None:  # this means there is no required header
        return
    else:
        if incoming_header := request.headers.get(APP_NAME):
            log.info("Found incoming header: %s", incoming_header)
            if incoming_header == looking_for:
                return
            else:
                log.warning(
                    "Request had the correct header name, but the value was incorrect"
                )
        else:
            log.warning("Request did not have the required header")

    if cfg.stealth_mode:
        return STEALTH_RESPONSE
    else:
        return web.json_response(
            {"error": "header was either missing or incorrect"}, status=400
        )


def check_locked_mode(request: web.Request) -> None | web.Response:
    "None = UNLOCKED | web.Response = LOCKED"

    client: TrueNASClient | None = request.app["truenas_client"]
    if client:
        return

    cfg: Config | AppBaseConfig = request.app["config"]
    if cfg.stealth_mode:
        return STEALTH_RESPONSE
    else:
        return web.json_response({"error": "TrueNAS API Client is locked"}, status=400)


def security_checks(request: web.Request) -> None | web.Response:
    "None = PASS | web.Response = FAIL"

    if bad_response := check_request_header(request):
        return bad_response

    if app_locked := check_locked_mode(request):
        return app_locked


# <><><><> ENDPOINTS <><><><>

# in Aiohttp, the callback endpoint functions must always take a web.Request
# object as the only argument.


async def request_handler(request: web.Request) -> web.Response:
    "Take request in json-rpc, return response in json-rpc"

    # This is the callback for the /request endpoint
    # The API for the client is simple. The client class can be
    # called as an awaitable function.

    log.info("Request received")

    if security_fail := security_checks(request):
        return security_fail

    try:
        payload = await request.json()  # JSON-RPC payload
    except json.JSONDecodeError as e:
        log.error("Malformed request, skipping: %s", e)
        return web.json_response({"error": "Malformed request"}, status=400)

    log.info("Request payload: %s", payload)

    client: TrueNASClient = request.app["truenas_client"]
    assert client is not None
    result = await client.call(payload)
    log.info("Request successful")
    log.debug("Response: %s,", result)

    # Return result back to CLI as JSON
    return web.json_response(result)


async def status(request: web.Request) -> web.Response:
    "Check the status of the TrueNAS API Conduit service"

    log.info("Status request received")

    if security_fail := security_checks(request):
        return security_fail

    client: TrueNASClient = request.app["truenas_client"]
    result = await client.status()
    log.info("Status request successful")
    return web.json_response(result)


async def stop(request: web.Request) -> web.Response:

    log.info("Stop command received")

    if security_fail := security_checks(request):
        return security_fail

    request.app["shutdown_event"].set()

    return web.json_response({"result": "Stopping the conduit service"})


async def restart(request: web.Request) -> web.Response:

    log.info("Restart command received")

    if security_fail := security_checks(request):
        return security_fail

    async def _restart() -> None:
        await asyncio.sleep(0.2)
        await request.app.cleanup()

        cfg_dump = request.app["config"].model_dump_json(context={"unmask": True})

        # The execvp Chad Swap. We dump out the config to stdout then pipe that
        # into a new copy of this process. This is necessary because the user
        # may have started the service using the CLI in standalone mode or otherwise
        # started the service by piping a custom config into it. So we preserve
        # whatever they passed in when restarting.
        executable = sys.executable
        log.debug("Executable: %s", executable)
        try:
            read_fd, write_fd = os.pipe()
            os.write(write_fd, cfg_dump.encode())
            os.close(write_fd)
            os.dup2(read_fd, 0)
            os.close(read_fd)
            os.execvp(SERVICENAME, [SERVICENAME])
        except OSError as e:
            # HACK: If there's an error then the service will just die after
            # attempting to restart. Theoretically I could whip up a mechanism
            # for a hot restart, but I don't think that's necessary. Maybe
            # in the future.
            err_string = examine_os_error(e)
            if request.app["config"].log_level == "trace":
                raise
            else:
                log.error("Error restarting service: %s", err_string)

    asyncio.create_task(_restart())
    return web.json_response({"result": "Restarting the conduit service..."})


async def lock(request: web.Request) -> web.Response:

    log.info("Lock request received")

    if security_fail := security_checks(request):
        return security_fail

    try:
        payload = await request.json()  # JSON-RPC payload
    except json.JSONDecodeError as e:
        log.error("Malformed request, skipping: %s", e)
        return web.json_response({"error": "Malformed request"}, status=400)

    log.info("Request payload: %s", payload)

    client: TrueNASClient | None = request.app["truenas_client"]
    request.app["locked"] = True
    if client:
        if client.config:
            request.app["json_dict"] = client.config.model_dump()
        close_result = await client.close()
        log.debug(close_result)
        if close_result.is_closed:
            log.info("The TrueNAS websocket client closed itself gracefully")
        else:
            log.warning(close_result.msg)

    request.app["truenas_client"] = None
    request.app["truenas_task"] = None
    return web.json_response({"result": "Service has been locked"})


async def unlock(request: web.Request) -> web.Response:

    log.info("Unlock request received")

    if bad_response := check_request_header(request):
        return bad_response

    try:
        payload = await request.json()  # JSON-RPC payload
    except json.JSONDecodeError as e:
        log.error("Malformed request, skipping: %s", e)
        return web.json_response({"error": "Malformed request"}, status=400)

    log.info("Request payload: %s", payload)

    cfg: Config | AppBaseConfig = request.app["config"]

    try:
        crypt_key = payload["crypt_key"]
    except KeyError:
        log.error("crypt_key field is required in the request JSON")
        if cfg.stealth_mode:
            return STEALTH_RESPONSE
        else:
            return web.json_response(
                {"error": "Unlock password was missing or invalid"}, status=400
            )

    unlocker: Unlocker = request.app["unlocker"]
    unlock_result = await unlocker.unlock_key(crypt_key)
    if unlock_result is True:
        # Return result back to CLI as JSON
        return web.json_response({"result": "Service has been unlocked"})
    else:
        # must be an exception
        return web.json_response(
            {"result": "Unlock failed", "error": str(unlock_result)}, status=400
        )
