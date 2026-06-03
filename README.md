<picture class="only-github">
  <source media="(prefers-color-scheme: dark)" srcset="https://edward-jazzhands.github.io/assets/truenas-api-conduit/banner-dark-theme.png">
  <source media="(prefers-color-scheme: light)" srcset="https://edward-jazzhands.github.io/assets/truenas-api-conduit/banner-light-theme.png">
  <img src="https://edward-jazzhands.github.io/assets/truenas-api-conduit/banner-no-theme.png" alt="Textual Window banner">
</picture>


# TrueNAS API Conduit

[![badge](https://img.shields.io/badge/Requires_Python->=3.12-blue&logo=python)](https://python.org)
[![badge](https://img.shields.io/badge/license-MIT-blue)](https://opensource.org/license/mit)

A lightweight local service that holds a persistent, authenticated WebSocket connection to your TrueNAS instance and exposes it as a plain HTTP REST API. This can serve on localhost on your work computer, or run as a Docker container directly on the TrueNAS server.

## The Raison D'etre

To make a long story short, this entire project started because I wanted a way to hit the TrueNAS API from my laptop so that I could display some of my home server's stats, like CPU usage, temperature, etc, inside of a desktop widget (I use [Conky](https://github.com/brndnmtthws/conky)).

Sounds like a simple task, right? At first, I was just gonna use the REST API, until I learned that it's deprecated.

TrueNAS [deprecated the REST API in 25.04 and is removing it in 26.0](https://www.truenas.com/docs/scale/25.10/api/). Everything is moving to their WebSocket API. This is overall a good thing because websockets are great, but it creates a serious problem for anything external that consumed the REST API: dashboards, scripts, monitoring tools, home automation integrations, and so on.

So you might say just rewrite everything to use a websocket. The problem is that to use websockets properly, you have to hold the connection open. If you've ever tried to program that before, you know it gets complex very quickly. Then you need logic for reconnecting, retrying, request multiplexing, and so on. You're now building a full state machine just to make some API calls that used to be 5 lines of code. And you can't just rely on the brute force approach of reconnecting for every request, because TrueNAS rate-limits this: exceed 20 auth attempts in 60 seconds and you'll get locked out for 10 minutes.

**TrueNAS API Conduit holds the connection open for you.** The service connects and authenticates at startup, then keeps that connection alive indefinitely. Your scripts and dashboards talk to a plain HTTP endpoint on localhost. The TrueNAS API essentially exists as a local OS service with a normal REST API, which you can curl, write your own programs to use, or access however else you feel like.

**It's also 50x faster than using the REST API directly.** If you're currently calling the TrueNAS REST API, your existing tools can get a roughly 50x speed increase. The average response time for a REST API call (using curl) is usually in the 400-500ms range. TrueNAS API Conduit brings that down to 10-20ms per request.

**A full proper CLI is built-in.** I've been creating CLI and TUI programs using Python for a few years, and my CLI game is pretty great. Rich help menus make it very easy to use and navigate, and it properly respects stdout/stderr separation so you can pipe the output into other programs (explained more in docs).

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
hyperfine 'curl -X POST http://localhost:4567/rpc -H "Content-Type: application/json" -d "{\"method\": \"core.ping\", \"params\": []}"'

  Time (mean ± σ): 9.7 ms ± 0.6 ms [User: 4.4 ms, System: 1.9 ms]
  Range (min … max): 9.0 ms … 12.9 ms 289 runs
```

**~50x faster per request**, for the entire lifetime of the service. Every tool that talks to your NAS gets this for free. They can also all share the one persistent websocket connection.

## How It Works

<picture>
  <img src="https://edward-jazzhands.github.io/assets/truenas-api-conduit/truenas-api-conduit.drawio.svg" style="max-width:100%;height:auto;"/>
</picture>

<p>The conduit is a 12-factor style service: it reads configuration from environment variables and a config file, and writes logs to stdout. You can run it as a Docker container, a system service, or a plain foreground process.</p>

## Documentation

For detailed guides, installation, and usage, see documentation:

### [Click here for documentation](https://github.com/edward-jazzhands/truenas-api-conduit/blob/main/docs/docs.md)

## Questions, Issues, Suggestions?

Use the [issues](https://github.com/edward-jazzhands/truenas-api-conduit/issues) section for bugs, issues, ideas or feature requests.

## Thanks and Copyright

MIT. See LICENSE file.

TrueNAS Copyright [iX Systems](https://www.ixsystems.com/)