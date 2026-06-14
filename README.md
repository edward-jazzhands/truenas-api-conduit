<picture class="only-github">
  <source media="(prefers-color-scheme: dark)" srcset="https://edward-jazzhands.github.io/assets/truenas-api-conduit/banner-dark-theme.png">
  <source media="(prefers-color-scheme: light)" srcset="https://edward-jazzhands.github.io/assets/truenas-api-conduit/banner-light-theme.png">
  <img src="https://edward-jazzhands.github.io/assets/truenas-api-conduit/banner-no-theme.png" alt="Textual Window banner">
</picture>

# TrueNAS API Conduit

[![badge](https://img.shields.io/badge/Requires_Python->=3.12-blue&logo=python)](https://python.org)
[![badge](https://img.shields.io/badge/license-MIT-blue)](https://opensource.org/license/mit)
[![badge](https://img.shields.io/badge/Badges_In_Readme-5-blue)](https://en.wikipedia.org/wiki/Mathematics)
[![badge](https://img.shields.io/badge/Made_In-Canada-red)](https://canadiantire.ca)
[![badge](https://img.shields.io/badge/Coded_By_Hand-95%-green)](https://www.reddit.com/r/notvibecoded/)

A lightweight local service that holds a persistent, authenticated WebSocket connection to your TrueNAS instance and exposes it as a plain HTTP REST API. This can serve on localhost on your laptop/main computer, or run as a Docker container directly on the TrueNAS server.

Note to humans: Hi, my name is Edward Jazzhands. This readme is not AI generated, a human actually typed this shit up using brain cells and calories. Same thing with the code. I'm a python expert (close to, at least) and this project is made almost entirely of hand-written code, with no coding agent used in development whasoever. Where I do use AI, it's to tutor me on how to code difficult things so I can learn faster while writing most of the code myself.

## The Raison D'etre

To make a long story short, this entire project started because I wanted a way to hit the TrueNAS API from my laptop so that I could display some of my home server's stats, like CPU usage, temperature, etc, inside of a desktop widget (I use [Conky](https://github.com/brndnmtthws/conky)).

Sounds like a simple task, right? Surely it can't be that complex. Fun little weekend project, I thought.

TrueNAS [deprecated the REST API in 25.04 and is removing it in 26.0](https://www.truenas.com/docs/scale/25.10/api/). Everything is moving to their WebSocket API. This is overall a good thing because websockets are great, but it creates a serious problem for anything external that consumed the REST API. In my case, my basic bash and curl script that I was gonna use to get the server stats into Conky. But I'd imagine this will also apply to any dashboards, monitoring tools, home automation integrations, and so on that are still on the REST API.

So you might say just rewrite everything to use a websocket. The problem is that to use websockets properly, you have to hold the connection open. If you've ever tried to program that before, you know it gets complex very quickly. Then you need logic for reconnecting, retrying, request multiplexing, and so on. You're now building a full state machine just to make some API calls that used to be 3 lines of code. And you can't just rely on the brute force approach of reconnecting for every request, because TrueNAS rate-limits this: if you exceed 20 auth attempts in 60 seconds, you'll get locked out for 10 minutes.

**TrueNAS API Conduit holds the connection open for you.** The service connects and authenticates at startup, then keeps that connection alive indefinitely. Your scripts and dashboards talk to a plain HTTP endpoint on localhost. After installing as an OS-level background service on your laptop, the TrueNAS API has been turned into an OS background service with a normal REST API, which you can curl, write your own programs to use, or access however else you feel like.

It's also 50x faster than using the REST API directly. If you're currently calling the TrueNAS REST API, your existing tools can get a roughly 50x speed increase. The average response time for a REST API call (using curl) is usually in the 400-500ms range. TrueNAS API Conduit brings that down to 10-20ms per request because it doesn't have to do the authentication dance every time.

Also, **a full proper CLI is built-in.** I've been creating CLI and TUI programs using Python for a few years, and my CLI game is pretty great. Rich help menus make it very easy to use and navigate (courtesey of [Rich-Click](https://github.com/ewels/rich-click)), and it properly respects stdout/stderr separation so you can pipe the output into other programs (explained more in docs).

### Benchmarks

Benchmarks were done with [hyperfine](https://github.com/sharkdp/hyperfine).

Calling `core.ping` directly against the TrueNAS REST API (plug in your API key and server's HTTPS address to try it yourself):

```sh
hyperfine 'curl -k -H "Authorization: Bearer YOUR-API-KEY" https://192.168.1.69:8443/api/v2.0/core/ping'

  Time (mean ± σ):     479.8 ms ± 135.4 ms    [User: 9.0 ms, System: 1.7 ms]
  Range (min … max):   431.2 ms … 864.8 ms    10 runs
``` 

Calling the same method through TrueNAS API Conduit:

```sh
hyperfine 'curl -X POST http://localhost:4567/rpc -d "{\"method\": \"core.ping\", \"params\": []}"'

  Time (mean ± σ): 9.7 ms ± 0.6 ms [User: 4.4 ms, System: 1.9 ms]
  Range (min … max): 9.0 ms … 12.9 ms 289 runs
```

**~50x faster per request**, for the entire lifetime of the service. Every tool that talks to your NAS gets this for free. They can also all share the one persistent websocket connection.

## Features

- Supports all TrueNAS API methods (the service passes through the request directly, it has no knowledge of what methods are available).
- Full color CLI is built-in, with thorough help menus, and meticulously designed by a human that thought about how it feels to use it (built with [rich-click](https://github.com/ewels/rich-click)).
- Returns the server's response verbatim as JSON, so you can pipe it into jq to filter and format the results.
- The service works by providing a simple REST API which can be used by curl, wget, or any other tool that can make HTTP requests. You could also write your own program to use the service.
- Includes a --filter option in the CLI to make it easier to use the API (This is different from using jq as it filters the results server-side instead of client-side, and you can combine server filters with client filters).
- Install as a system service on Linux, Mac, or Windows.
- Install as a Docker container directly on your TrueNAS server.
- Run in standalone/foreground mode (no install required).
- Keyring integration so you can avoid storing your TrueNAS API key in plain text.
- Fallback file encryption for the API key for usage in minimal environments.

## How It Works

![Architecture Diagram](https://edward-jazzhands.github.io/assets/truenas-api-conduit/truenas-api-conduit.drawio.svg)

The conduit is a 12-factor style service: it reads configuration from environment variables and a config file, and writes logs to stdout. You can run it as a Docker container, a system service, or a plain foreground process.

# TODO: Quick demonstration of using it to get server stats into Conky

## Documentation

For detailed guides, installation, and usage, see documentation:

### [Click here for documentation](https://github.com/edward-jazzhands/truenas-api-conduit/blob/main/docs/docs.md)

## Questions, Issues, Suggestions?

Use the [issues](https://github.com/edward-jazzhands/truenas-api-conduit/issues) section for bugs, issues, ideas or feature requests.

## Thanks and Copyright

MIT. See LICENSE file.

TrueNAS Copyright [iX Systems](https://www.ixsystems.com/)

Made possible by utilizing these awesome third-party Python libraries:

- [pydantic-settings](https://github.com/pydantic/pydantic-settings)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- [websockets](https://github.com/python-websockets/websockets)
- [requests](https://github.com/psf/requests)
- [rich](https://github.com/Textualize/rich)
- [click](https://github.com/pallets/click)
- [rich-click](https://github.com/ewels/rich-click)
- [click-didyoumean](https://github.com/click-contrib/click-didyoumean)
- [keyring](https://github.com/jaraco/keyring)
- [secretstorage](https://github.com/mitya57/secretstorage)
- [cryptography](https://github.com/pyca/cryptography)
- [psutil](https://github.com/giampaolo/psutil)
- [yaspin](https://github.com/pavdmyt/yaspin)
- [platformdirs](https://github.com/platformdirs/platformdirs)
