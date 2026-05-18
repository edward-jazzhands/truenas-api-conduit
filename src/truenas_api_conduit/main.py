# standard library
import asyncio
from typing import Any, Final
import json
import os
import sys
import time
from pathlib import Path
import logging

# third party
import websockets
import websockets.client
import websockets.legacy.client as legacy_client
import websockets.exceptions

# project
# from truenas_api_conduit.user_config import CFG

log = logging.getLogger(__name__)


# Server Metrics I want to collect:

#   - System uptime (days) - system.info
#   - CPU usage (percent)  - reporting.get_data "{"name":"cpu"}, {"start":$start,"end":$end,"aggregate":true}"
#   - CPU temperature (degrees C)
#   - RAM usage (percent)
#   - Disk usage (percent) - disk.query or possibly pool.query
#   - Network usage (bytes/s)
#   - Number of active alerts - alert.list


async def websocket_send(
    ws: websockets.client.WebSocketClientProtocol, 
    json_dict: dict, 
    req_id: int
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


async def session():
    

    # Websockets are asynchronous, the server doesn't guarantee it will
    # respond to your messages in the same order you sent them.
    # The id field is part of the JSON-RPC 2.0 spec and is used to match
    # responses to requests.
    req_id = 1

    async with legacy_client.connect(URI) as ws:

        auth_request_dict ={
            "id": req_id,
            "jsonrpc": "2.0",
            "method": "auth.login_with_api_key",
            "params": [TRUENAS_API_KEY]
        }

        # --- AUTHENTICATION ---
        # We do this once at the start of the session rather than on every request, 
        # this is the main advantage of a persistent websocket over a REST API
        await ws.send(json.dumps(auth_request_dict))
        received = await ws.recv()

        auth_response = json.loads(received)
        if not auth_response.get("result"):
            print("Authentication failed", file=sys.stderr)
            return
        print("Authenticated.")
        req_id += 1 

        # --- POLL LOOP ---
        # This runs forever (until the connection drops or the script is killed).
        # Each iteration sends one request, waits for its response, writes the
        # result to disk for Conky to read, then sleeps before doing it again.

        while True:

            # NOTE: These request dicts must be inside of the while loop 
            # for req_id to increment

            # contains the result.uptime stat
            system_info = {
                "id": req_id,
                "jsonrpc": "2.0",
                "method": "system.info",
                "params": []
            }

            # contains result.size, result.allocated, result.free
            pool_query = {
                "id": req_id+1,
                "jsonrpc": "2.0",
                "method": "pool.query",
                "params": []
            }

            msg = await websocket_send(ws, system_info, req_id)

            uptime = msg.get("result", {}).get("uptime")
            if uptime is not None:
                # Write atomically to a temp file then replace, so Conky never
                # reads a half-written file mid-update
                tmp = UPTIME_OUTPUT.with_suffix(".tmp")
                tmp.write_text(str(uptime) + "\n")
                tmp.replace(UPTIME_OUTPUT)

                print(f"Uptime: {uptime}")
            else:
                print(f"Unexpected response: {msg}", file=sys.stderr)


            req_id += 1
            await asyncio.sleep(POLL_INTERVAL)



async def start():
    # Wraps the entire session so that if the connection drops for any
    # reason, we wait a few seconds and try again.
    while True:
        try:
            await session()
        except websockets.exceptions.InvalidURI as e:
            # A bad URI means something is wrong with TRUENAS_HOST in .env.
            # No point retrying since it'll never work without a fix.
            print(f"Invalid URI: {e}", file=sys.stderr)
            sys.exit(1)
        except (websockets.exceptions.WebSocketException, OSError) as e:
            print(f"Connection error: {e}, reconnecting in {RECONNECT_DELAY}s...", file=sys.stderr)
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            print(f"Unexpected error: {e}, reconnecting in {RECONNECT_DELAY}s...", file=sys.stderr)
            await asyncio.sleep(RECONNECT_DELAY)

