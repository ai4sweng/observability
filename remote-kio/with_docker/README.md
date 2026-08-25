# with_docker — run the same demo KIO in a container

Identical behavior to [`../with_script`](../with_script), but packaged as a
container. Good when you want the KIO to run the same way it would in a real
deployment, without installing Python locally.

## Run it

```bash
cp .env.example .env          # then edit KIO_ID and the endpoint/token
docker compose up --build     # add -d to run in the background
docker compose logs -f        # watch it push (Ctrl-C detaches)
docker compose down           # stop
```

Within ~30–60s your `KIO_ID` shows up in Grafana → **KIO Detail** (the `KIO`
dropdown), and the panels start filling. The log lines land in the logs panel.

## The one gotcha: the endpoint address

Inside a container, `localhost` means the container itself — **not** your host.
Set `OTEL_EXPORTER_OTLP_ENDPOINT` in `.env` accordingly:

| Where the collector runs | Endpoint |
|--------------------------|----------|
| The **same host** as this container | `http://host.docker.internal:4317` |
| A **different machine** (LAN) | `http://<platform-ip>:4317` |
| A **different network** (Tailscale/VPN) | `http://<tailscale-ip>:4317` |

Finding the address and opening the firewall: [`../NETWORK.md`](../NETWORK.md).

## What's inside

`kio_otel.py` + `main.py` are the same files as `with_script` (kept as copies so
this folder is self-contained). To plug in your real KIO, edit `do_one_request()`
in `main.py`, or copy `kio_otel.py` into your own project and instrument it there.
