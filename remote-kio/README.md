# remote-kio — connect a KIO to the central observability platform

This folder is a **hands-on starter kit** for any team whose KIO needs to send
telemetry to the AI4SWENG central platform (OTel Collector → VictoriaMetrics /
VictoriaLogs / Tempo → Grafana). It works whether your KIO runs on the same
machine, elsewhere on the LAN, or on another network (Tailscale/VPN) — the
architecture is push-based, so only one address changes.

> The **normative guide** is the *AI4SWENG Observability Integration Guide*
> (the shared document). This folder is the runnable, copy-and-edit companion to it.

## Pick a path

| I want to… | Go to |
|------------|-------|
| **See it work in 2 minutes** with plain Python | **[`with_script/`](with_script/)** — `cp .env.example .env`, edit, `python main.py` |
| **See it work in a container** | **[`with_docker/`](with_docker/)** — `cp .env.example .env`, edit, `docker compose up --build` |
| **Connect my own module** (full walkthrough) | **[`INTEGRATION.md`](INTEGRATION.md)** |
| Sort out **network / firewall / static IP / Tailscale** | **[`NETWORK.md`](NETWORK.md)** |

## How the examples work

Both `with_script` and `with_docker` run the same tiny demo KIO: it reads
everything from `.env` and pushes the **seven mandatory metrics + a 60s heartbeat
+ log lines** to the platform. Watch your `KIO_ID` appear in Grafana → **KIO
Detail** within ~30–60s.

The two files that matter:
- **`main.py`** — the demo loop. Replace `do_one_request()` with your real work.
- **`kio_otel.py`** — the instrumentation helper (the mandatory metrics, heartbeat,
  trace and log streams). Copy this one file into your real project; you don't edit it.

Configuration lives entirely in `.env` (kio id, endpoint, bearer token, timing).
The central collector **requires a bearer token** — set it in `.env`
(`OTEL_EXPORTER_OTLP_HEADERS`). Sending metrics beyond the mandatory seven
(custom / self-service) is covered in the Integration Guide.

## First time? Verify connectivity before anything else

```bash
cd with_script
pip install -r requirements.txt
OTEL_EXPORTER_OTLP_ENDPOINT=http://<platform-host>:4317 python check_connectivity.py
```

A `PASS` means the network path and auth are good. If it fails, it's almost
always the firewall or the wrong address — see [`NETWORK.md`](NETWORK.md).
