# standard library
import asyncio
from typing import Any, Final, TYPE_CHECKING
import json
import os
import sys
import time
import ssl
from pathlib import Path
import logging

if TYPE_CHECKING:
    from truenas_api_conduit.user_config import Config

# third party
import websockets
import websockets.client
import websockets.legacy.client as legacy_client
import websockets.exceptions

# project
from truenas_api_conduit.setup_app_dir import CONFIG_DIR
import truenas_api_conduit.api_requests as api_requests

log = logging.getLogger(__name__)

UPTIME_OUTPUT = Path("/tmp") / "uptime.txt"
RECONNECT_DELAY = 5

# Server Metrics I want to collect:

#   - System uptime (days) - system.info
#   - CPU usage (percent)  - reporting.get_data "{"name":"cpu"}, {"start":$start,"end":$end,"aggregate":true}"
#   - CPU temperature (degrees C)
#   - RAM usage (percent)
#   - Disk usage (percent) - disk.query or possibly pool.query
#   - Network usage (bytes/s)
#   - Number of active alerts - alert.list


async def websocket_send(
    ws: websockets.client.WebSocketClientProtocol, json_dict: dict, req_id: int
) -> dict[str, Any]:

    await ws.send(json.dumps(json_dict))

    # We can't just do ws.recv() once and assume it's our response.
    # The server may send unsolicited messages (alerts, events, etc.)
    # at any time on the same connection. So we loop on recv() and
    # discard anything that doesn't match the ID we just sent.
    # Usually this will just be a single response, but you need the safety.

    extra_responses = 0
    while True:
        raw = await ws.recv()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"Malformed response, skipping: {e}", file=sys.stderr)
            continue
        if msg.get("id") == req_id:
            break  # This is the response we were waiting for
        else:
            extra_responses += 1
            print(f"Discarded {extra_responses} extra responses", file=sys.stderr)

    # HACK: This assumes the response we're waiting for does in fact arrive,
    # otherwise we'll get stuck in a loop waiting for it.
    return msg


async def session(cfg: Config):

    log.info("Starting daemon session")

    # Websockets are asynchronous, the server doesn't guarantee it will
    # respond to your messages in the same order you sent them.
    # The id field is part of the JSON-RPC 2.0 spec and is used to match
    # responses to requests.
    req_id = 1

    # Create an SSL context that skips certificate verification
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if cfg.validate_certs:
        if cfg.truenas_cert_path is not None:
            cert_path_obj = Path(cfg.truenas_cert_path)
            ssl_context.load_verify_locations(cafile=cert_path_obj)
        # if validating but there's no cert path, it must be because the cert is
        # from a trusted CA, which websockets will automatically validate.
    else:
        # required before you can disable cert verification:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    async with legacy_client.connect(cfg.uri, ssl=ssl_context) as ws:

        auth_request_dict = {
            "id": req_id,
            "jsonrpc": "2.0",
            "method": "auth.login_with_api_key",
            "params": [cfg.api_key],
        }

        # AUTHENTICATION
        await ws.send(json.dumps(auth_request_dict))
        received = await ws.recv()

        auth_response = json.loads(received)
        if not auth_response.get("result"):
            log.error("Authentication failed")
            return
        log.info("Authenticated.")
        req_id += 1

        # POLL LOOP
        while True:
            # This runs forever (until the connection drops or the script is killed).
            # Each iteration sends one request, waits for its response, writes the
            # result to output files, then sleeps

            # NOTE: These request dicts must be inside of the while loop
            # for req_id to increment

            # contains the result.uptime stat
            system_info = api_requests.system_info(req_id)

            # contains result.size, result.allocated, result.free
            pool_query = api_requests.pool_query(req_id)

            msg = await websocket_send(ws, system_info, req_id)

            uptime = msg.get("result", {}).get("uptime")
            if uptime is not None:
                # Write atomically to a temp file then replace, so programs never
                # reads a half-written file mid-update
                tmp = UPTIME_OUTPUT.with_suffix(".tmp")
                tmp.write_text(str(uptime) + "\n")
                tmp.replace(UPTIME_OUTPUT)

                log.debug(f"Uptime: {uptime}")
            else:
                log.error(f"Unexpected response: {msg}")

            req_id += 1
            log.info("Sleeping for %s seconds", cfg.polling_interval)
            await asyncio.sleep(cfg.polling_interval)


async def session_wrapper(cfg: Config):
    # Wraps the entire session so that if the connection drops for any
    # reason, we wait a few seconds and try again.
    while True:
        try:
            await session(cfg)
        except ssl.SSLError as e:
            # SSL errors mean the HTTPS connection is not working, often due to
            # a bad certificate.
            log.error(
                f"SSL error: {e}\n"
                "You can fix this by:\n"
                "  - Using a trusted certificate signed by a CA\n"
                "  - Setting truenas_cert_path to the path of your self-signed certificate\n"
                "  - Setting validate_certs to False\n"
            )
            sys.exit(1)
        except (websockets.exceptions.InvalidURI, ssl.SSLError) as e:
            # A bad URI means something is wrong with TRUENAS_HOST in .env.
            # Regular error since the user should be able to fix it.
            log.error(f"Connection error: {e}")
            sys.exit(1)
        except (websockets.exceptions.WebSocketException, OSError) as e:
            log.error(
                f"Connection error: {e}, reconnecting in {RECONNECT_DELAY}s...",
            )
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(
                f"Unexpected error: {e}, reconnecting in {RECONNECT_DELAY}s...",
            )
            await asyncio.sleep(RECONNECT_DELAY)


def start(cfg: Config):
    asyncio.run(session_wrapper(cfg))
