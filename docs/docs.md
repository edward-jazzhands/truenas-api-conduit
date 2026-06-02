# TrueNAS API Conduit Documentation

## Installation

Recommended way to install is using [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/stable/).

### Option 1: uv

```sh
uv tool install truenas-api-conduit
```

You can then run the command:

```sh
truenas-api
```

### Option 2: pipx

PipX install works the same as UV Tool install. I personally recommend UV over Pipx (its faster) but they do the same thing here.

```sh
pipx install truenas-api-conduit
```

Just like with uv, you can now run `truenas-api` globally.

### Option 3: Clone and run

This project is managed using UV so its recommended to run it using UV if you clone it.

```sh
git clone https://github.com/yourusername/truenas-api-conduit.git
cd truenas-api-conduit
uv sync
uv run truenas-api
```

### Option 4: Docker / docker-compose

See the [Docker section](#docker--docker-compose) below.

## Running the Service

### Directly

```sh
export TRUENAS_HOST=192.168.1.100
export TRUENAS_API_KEY=your-api-key-here

truenas-conduit start
```

### As a system service

The CLI includes an installer that sets the service up under systemd (Linux) or launchd (macOS):

```sh
truenas-conduit install
```

This writes the appropriate service definition and enables it to start on boot. You will be prompted for your configuration values if they aren't already set.

To remove the service:

```sh
truenas-conduit uninstall
```

### Configuration

THIS SECTION IS NOT WRITTEN YET.

### Docker / docker-compose

You can enter environment values directly into your compose file, but its better to place them in a file called .env beside the compose file and let Docker read from it (Docker does this automatically).

```yaml
services:
  truenas-conduit:
    image: edward-jazzhands/truenas-api-conduit:latest
    restart: unless-stopped
    user: '${UID}:${GID}'
    environment:
      TRUENAS_HOST: ${TRUENAS_HOST}
      API_KEY: ${API_KEY}
      TRUENAS_CERT_PATH: ${TRUENAS_CERT_PATH}
      VALIDATE_CERTS: ${VALIDATE_CERTS}
      API_ROUTE: ${API_ROUTE}
      LOG_LEVEL: ${LOG_LEVEL}
      NO_COLOR: ${NO_COLOR}
      SOCKET_PORT: ${SOCKET_PORT}
      SERVICE_ADDRESS: ${SERVICE_ADDRESS}
    volumes:
      # Bind mount the folder containing your config file to use it
      # See Configuration section in docs (above)
      - ${CONFIG_DIR}:/config
    ports:
      # bind to loopback only
      - "127.0.0.1:4567:4567" 

```

```sh
docker compose up -d
```

#### Running on the TrueNAS host itself

The conduit can run as a Docker container directly on your TrueNAS server. The WebSocket hop becomes a container-local network call, and the per-request latency remains in the 10–15ms range. Other containers can then reach the conduit without it being exposed to your broader network at all:

```yaml
services:
  truenas-conduit:
    image: yourusername/truenas-api-conduit:latest
    restart: unless-stopped
    environment:
      TRUENAS_HOST: 192.168.1.100
      TRUENAS_API_KEY: your-api-key-here
      TRUENAS_VERIFY_SSL: "false"
    # No ports: block -- only reachable by containers on this network
    networks:
      - dashboard_net

  homepage:
    image: ghcr.io/gethomepage/homepage:latest
    # ... your existing homepage config
    networks:
      - dashboard_net

networks:
  dashboard_net:
```

With this setup, the conduit is only reachable by containers on `dashboard_net` and is never exposed to your LAN. Scope your API key to read-only access to reduce the blast radius further.

---

## Usage

The conduit exposes a single endpoint: `POST /rpc`

The request body mirrors the TrueNAS JSON-RPC 2.0 method call format:

```json
{
  "method": "core.ping",
  "params": []
}
```

```sh
curl -X POST http://localhost:4567/rpc \
  -H "Content-Type: application/json" \
  -d '{"method": "core.ping", "params": []}'
```

Response:

```json
{"jsonrpc": "2.0", "result": "pong", "id": 2}
```

Note the request ID increments with usage.

A more practical example, fetching pool status (assumes you have jq installed):

```sh
curl -X POST http://localhost:4567/rpc \
  -H "Content-Type: application/json" \
  -d '{"method": "pool.query", "params": []}' | jq
```

Any method available in the [TrueNAS WebSocket API](https://api.truenas.com) can be called this way.

### Filters and Params

THIS SECTION IS NOT WRITTEN YET.

### CLI Reference

THIS SECTION IS NOT WRITTEN YET.

### Health / Status Check

The conduit also exposes `GET /status` for container orchestration and monitoring. Again, assuming you have jq installed (you can remove the `| jq` at the end if you do not):

```sh
curl http://localhost:4567/status | jq
```

Sample output:

```json
{
  "authenticated": true,
  "req_id": 7,
  "ws_conn host": "192.168.1.69",
  "ws_conn port": 8443,
  "socket_port": 4567,
  "ws_conn secure": true,
  "truenas_cert_path": null,
  "validate_certs": false,
  "api_key": "2-HsvigfjT...",
  "log_level": "info",
  "no_color": false
}
```


## Security Notes

- By default the HTTP server binds to `127.0.0.1` only and is not reachable from other machines on your network.
- In Docker deployments, use a dedicated internal network rather than publishing the port, unless you specifically need external access.
- Create a dedicated TrueNAS user account with a scoped API key for the conduit rather than using an admin key.
- The conduit does not implement its own authentication -- it is designed to be a localhost/internal-network service. Do not expose it to the public internet.
