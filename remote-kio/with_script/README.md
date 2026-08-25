# with_script — run a demo KIO directly with Python

The simplest way to see a KIO appear on the central platform: a small terminal
program that pushes the mandatory telemetry (7 metrics + heartbeat + logs) using
values from `.env`. No Docker required.

## Run it

```bash
cp .env.example .env          # then edit KIO_ID and the endpoint/token
pip install -r requirements.txt

python check_connectivity.py  # optional but recommended: proves you can reach the platform
python main.py                # starts pushing; Ctrl-C to stop
```

Within ~30–60s your `KIO_ID` shows up in Grafana → **KIO Detail** (the `KIO`
dropdown), and the panels start filling. The log lines land in the logs panel.

## What each file is

| File | Purpose |
|------|---------|
| `main.py` | The demo loop. **Edit `do_one_request()`** to plug in your real work. |
| `kio_otel.py` | The instrumentation helper (the 7 mandatory metrics, heartbeat, trace, logs). Copy this into your real project — you don't edit it. |
| `check_connectivity.py` | Preflight: TCP reach + one real test export. Run it first if nothing shows up. |
| `.env.example` | All configuration. Copy to `.env` and edit. |
| `requirements.txt` | The three OpenTelemetry packages. |

## Connecting from a different machine

Only `OTEL_EXPORTER_OTLP_ENDPOINT` changes — point it at the platform host's
address (LAN IP, or a Tailscale IP). The platform host must allow inbound
traffic on the OTLP port. See [`../NETWORK.md`](../NETWORK.md).

## Using your own module instead of this demo

This is a demo loop. For your real KIO: copy `kio_otel.py` into your project,
construct `KIOTelemetry()` once at startup, wrap each unit of work in
`with kio.request(...) as req:`, and call `kio.shutdown()` before exit. Sending
metrics beyond the mandatory 7 (custom / self-service) is covered in the
official Integration Guide.
