import pytest
import asyncio
import os
from unittest.mock import MagicMock, AsyncMock, Mock

# from typing import Any
# from pathlib import Path

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_config():
    
    cfg = Mock()
    cfg.truenas_host = "192.168.1.100"
    cfg.truenas_port = 6000
    cfg.api_key = Mock()
    cfg.api_key.get_secret_value = Mock(return_value="test-api-key-12345")
    cfg.socket_port = 4567
    cfg.service_address = "127.0.0.1"
    cfg.request_header = "X-Test-Header"
    cfg.log_level = "info"
    cfg.validate_certs = False
    cfg.truenas_cert_path = None
    cfg.uri = "wss://192.168.1.100:6000/api/current"
    cfg.no_color = False
    return cfg


@pytest.fixture
def mock_env_vars():

    return {
        "TRUENAS_HOST": "192.168.1.100:6000",
        "TRUENAS_API_KEY": "test-api-key-12345",
        "TRUENAS_CERT_PATH": None,
        "TRUENAS_VALIDATE_CERTS": "false",
        "TRUENAS_LOG_LEVEL": "info",
        "TRUENAS_SOCKET_PORT": "4567",
        "TRUENAS_SERVICE_ADDRESS": "127.0.0.1",
        "TRUENAS_API_ROUTE": "/api/current",
        "TRUENAS_REQUEST_HEADER": "X-Test-Header",
        "TRUENAS_CRYPT_KEY": "crypt-key-12345",
        "RICH_CLICK_THEME": None,
        "NO_COLOR": None,
        "EDITOR": None,
    }


@pytest.fixture
def mock_websocket():
    
    # this is what websockets.connect() would normally return.
    # it's an AsyncMock so we can await it and track calls.
    # purposes:
    # - tracking what was sent/received
    # - simulating server responses
    # - testing connection failures

    ws = AsyncMock()
    ws._host = "192.168.1.100"
    ws._port = 6000
    ws._secure = True
    
    # make recv() return test data by default
    ws.recv = AsyncMock(return_value='{"id": 1, "jsonrpc": "2.0", "result": "pong"}')
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    
    return ws


@pytest.fixture
def mock_app():
    # endpoint testing. Includes:
    # - Mock config
    # - Mock TrueNAS client
    # - Mock shutdown event

    app = MagicMock()
    app.__getitem__ = MagicMock()
    app.__setitem__ = MagicMock()
    app.cleanup = AsyncMock()
    
    # Set up default returns for app["key"] access
    app_data = {}
    app.__getitem__.side_effect = lambda key: app_data.get(key)
    app.__setitem__.side_effect = lambda key, value: app_data.update({key: value})
    
    app["config"] = Mock()
    app["config"].request_header = "X-Test-Header"
    app["config"].log_level = "info"
    
    app["truenas_client"] = AsyncMock()
    app["shutdown_event"] = MagicMock()
    
    return app


@pytest.fixture
def mock_request(mock_config):
    # mock aiohttp web.Request.
    #  Simulates an HTTP request with:
    #  - Headers
    #  - JSON body support
    #  - Reference to the app

    request = AsyncMock()
    request.app = {}
    request.app["config"] = mock_config
    request.app["truenas_client"] = AsyncMock()
    request.headers = {"truenas-api-conduit": mock_config.request_header}
    request.json = AsyncMock()
    
    return request


@pytest.fixture
async def configured_truenas_client(mock_config, event_loop):
    
    # finally bring in the real client class but using mock config/loop.
    from truenas_api_conduit.core.ws_client import TrueNASClient
    
    client = TrueNASClient(mock_config, event_loop)
    
    # NOTE: Context manager pattern. One yield, startup in top half,
    # cleanup in bottom half.
    yield client
    
    # ensure pending futures are cleared
    client._cleanup_pending()


async def test_real_startup(mock_env_vars):

    #! LAST THOUGHT: we need a way for pytest-asyncio to be using the same event
    # loop that we will pass in to the start() function.
    
    from truenas_api_conduit.core import __main__

    for key, value in mock_env_vars.items():
        if value is not None:
            os.environ[key] = value
        else:
            del os.environ[key]
    
    # NOTE: This is the main entry point for the service. This initializes
    # the current configuration. Inside of this after loading the config
    # with pydantic-settings, it will do an asyncio.run() to start the
    # aiohttp web server.
    __main__.start()