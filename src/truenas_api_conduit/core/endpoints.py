# standard library
import json
import os
import logging
import asyncio
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # ws_client contains the import for the websockets library so we gain
    # a little bit by making it a lazy import when its needed.
    from truenas_api_conduit.core.ws_client import TrueNASClient


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


# <><><><> HELPERS <><><><>


def check_request_header(request: web.Request) -> RequestHeader:
    "Check if the request has the required header"

    looking_for: str | None = request.app["config"].request_header

    if looking_for is None:  # this means there is no required header
        return RequestHeader.NONE
    else:
        if incoming_header := request.headers.get(APP_NAME):
            log.info("Found incoming header: %s", incoming_header)
            if incoming_header == looking_for:
                return RequestHeader.CORRECT
            else:
                log.warning("Request had the wrong header")
                return RequestHeader.INCORRECT
        else:
            log.warning("Request did not have the required header")
            return RequestHeader.MISSING


# <><><><> ENDPOINTS <><><><>

# in Aiohttp, the callback endpoint functions must always take a web.Request
# object as the only argument.


async def request_handler(request: web.Request) -> web.Response:
    "Take request in json-rpc, return response in json-rpc"

    # This is the callback for the /request endpoint
    # The API for the client is simple. The client class can be
    # called as an awaitable function.

    log.info("Request received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        return web.json_response({"error": "Incorrect header"}, status=400)

    try:
        payload = await request.json()  # JSON-RPC payload
    except json.JSONDecodeError as e:
        log.error("Malformed request, skipping: %s", e)
        return web.json_response({"error": "Malformed request"}, status=400)

    log.info("Request payload: %s", payload)

    client: TrueNASClient = request.app["truenas_client"]
    result = await client.call(payload)
    log.info("Request successful")
    log.debug("Response: %s,", result)

    # Return result back to CLI as JSON
    return web.json_response(result)


async def status(request: web.Request) -> web.Response:
    "Check the status of the TrueNAS API Conduit service"

    log.info("Status request received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        return web.json_response({"error": "Incorrect header"}, status=400)

    client: TrueNASClient = request.app["truenas_client"]
    result = await client.status()
    log.info("Status request successful")
    return web.json_response(result)


async def stop(request: web.Request) -> web.Response:

    log.info("Stop command received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        return web.json_response({"error": "Incorrect header"}, status=400)

    request.app["shutdown_event"].set()

    return web.json_response({"result": "Stopping the conduit service"})


async def restart(request: web.Request) -> web.Response:

    log.info("Restart command received")

    header = check_request_header(request)
    if header == RequestHeader.MISSING:
        return web.json_response({"error": "Missing header"}, status=400)
    elif header == RequestHeader.INCORRECT:
        return web.json_response({"error": "Incorrect header"}, status=400)

    async def _restart() -> None:
        await asyncio.sleep(0.2)
        await request.app.cleanup()

        cfg_dump = request.app["config"].model_dump_json(context={"unmask": True})

        # The execvp Chad Swap. We dump out the config to stdout then pipe that
        # into a new copy of this process. This is necessary because the user
        # may have started the service using the CLI in standalone mode or otherwise
        # started the service by piping a custom config into it. So we preserve
        # whatever they passed in when restarting.
        try:
            read_fd, write_fd = os.pipe()
            os.write(write_fd, cfg_dump.encode())
            os.close(write_fd)
            os.dup2(read_fd, 0)
            os.close(read_fd)
            os.execvp(SERVICENAME, [SERVICENAME])
        except OSError as e:
            err_string = examine_os_error(e)
            if request.app["config"].log_level == "trace":
                raise
            elif request.app["config"].log_level == "debug":
                log.exception("Error restarting service: %s", err_string)
            else:
                log.error("Error restarting service: %s", err_string)

    asyncio.create_task(_restart())
    return web.json_response({"result": "Restarting the conduit service"})
