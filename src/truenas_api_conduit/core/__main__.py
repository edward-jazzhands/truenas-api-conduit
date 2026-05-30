# standard library
import json
import os
import sys
import logging
import json
import sys
import signal
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # ws_client contains the import for the websockets library so we gain
    # a little bit by making it a lazy import when its needed.
    from truenas_api_conduit.core.ws_client import TrueNASClient

# third party
import pydantic
from aiohttp import web

# project
from truenas_api_conduit.console import console_stderr
import truenas_api_conduit.log_setup as log_setup
from truenas_api_conduit.config import Config


def handle_exit(*_):
    print("\nShutting down.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    signal.signal(signal.SIGQUIT, handle_exit)


log_setup.init_logging()
log = logging.getLogger(__name__)


async def request_handler(request: web.Request) -> web.Response:

    # This is the callback for the /rpc endpoint
    # The API for the client is simple. We only need to create the client,
    # run client.connect(), then it can be called as an awaitable function.

    payload = await request.json()  # JSON-RPC payload

    client: TrueNASClient = request.app["truenas"]
    result = await client(payload)
    log.debug(f"Response: {result}")

    # Return result back to CLI as JSON
    return web.json_response(result)


async def start_truenas(app: web.Application) -> None:

    from truenas_api_conduit.core.ws_client import TrueNASClient

    client = TrueNASClient(app["config"])  #  The client runs inside the web app
    app["truenas"] = client  
    await client.connect()  #  will handle the auth process


async def stop_truenas(app: web.Application) -> None:

    client: TrueNASClient = app["truenas"]
    await client.close()


def start():

    nc_env = os.environ.get("NO_COLOR")
    if nc_env is not None:
        console_stderr.no_color = True

    # recall the Config class is a pydantic-settings model from the config submodule.

    source: str = ""
    try:
        # PRIORITY OF CONFIG SOURCES:
        # 1. stdin (piped config)
        if not sys.stdin.isatty():
            source = "stdin"
            raw = sys.stdin.read()
            cfg = Config.model_validate_json(raw)
        # 2. env var TAC_CONFIG created by the CLI. Used if someone chooses the
        # --foreground option. CLI does os.execvp to start a new process
        elif os.environ.get("TAC_CONFIG"):
            source = "TAC_CONFIG"
            cfg = Config.model_validate_json(os.environ["TAC_CONFIG"])
        # 3. Normal load (env vars, config file, keyring)
        else:
            source = "standard"
            cfg = Config()
    except json.JSONDecodeError as e:
        log.critical(f"Malformed config JSON: {e}")
        sys.exit(1)
    except pydantic.ValidationError as e:
        log.critical(f"Configuration error: {e}")
        sys.exit(1)

    log_level: int = logging.getLogger().level
    log.info("Logging level is currently at %s", log_level)
    log.info("Config loaded from %s", source)
    log.debug("Config: %s", cfg)
    log.debug("Config provenance: %s", cfg.provenance)

    app = web.Application() # ! should I insert our logger here?
    app["config"] = cfg  #   so startup hooks can access it

    # HTTP endpoint for the CLI
    app.router.add_post("/rpc", request_handler)

    # NOTE: The reason we want the start and stop functions to be hooks is because
    # it allows us to make them async. Notice we don't need to use asyncio.run()
    # here. The aiohttp server will handle that for us.

    app.on_startup.append(start_truenas) # sets up websocket client
    app.on_cleanup.append(stop_truenas)  # closes websocket client

    # Starts:
    # - event loop
    # - HTTP server
    # - startup hooks (which connect websocket client)
    web.run_app(app, host="127.0.0.1", port=cfg.socket_port)

    # NOTE: address is hard-coded to localhost because this is a system service
    # and as such we don't want it to be possible to reach it from the outside.


if __name__ == "__main__":
    start()
