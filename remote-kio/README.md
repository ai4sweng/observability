# remote-kio — connecting a KIO from a different machine

This folder collects everything about connecting a KIO that runs on a
**physically separate machine** from the central observability stack
(Collector, VictoriaMetrics, VictoriaLogs, Tempo, Grafana). Since the
architecture is push-based, this means changing one environment variable
(`OTEL_EXPORTER_OTLP_ENDPOINT`), not any code.

## Which guide do I need? (start here)

| Your situation | Where to go |
|---|---|
| **I don't know this system at all, and I'm connecting my own KIO module from scratch** (with Docker or **without Docker**) | **[`INTEGRATION.md`](INTEGRATION.md)** — a complete, self-contained guide from scratch, plus the copyable kit [`reference-client/`](reference-client/) |
| I want to run your ready-made **simulator** on a different machine (test/demo) | The rest of this document (↓) |
| I only need **network / firewall / static IP / Tailscale** details | **[`NETWORK.md`](NETWORK.md)** |
| Connecting the real **FocusTracer (KIO2)** module | [`../kio2-integration/README.md`](../kio2-integration/README.md) |
| The normative **contract** (7 metrics, rules, reference code) | [`../observability_integration_contract.pdf`](../observability_integration_contract.pdf) |

---

## This package: running the simulator on a remote machine

The section below is a copyable, self-contained "lego piece" prepared for
running a single KIO with **our own simulator code** on a separate machine.
The main stack stays wherever it already is; only this package plus the
`kio-simulator/` source travel to the remote machine. (If you're connecting
your own code instead, go to [`INTEGRATION.md`](INTEGRATION.md) instead — you
don't need this simulator at all there.)

### Before sending this package to a KIO owner (before zipping)

Before zipping and sending the `observability/kio-simulator/` and
`observability/remote-kio/` folders as-is, remove the following —
`.gitignore` hides these from git, but a raw zip operation doesn't know about
`.gitignore` and still includes them:

- `remote-kio/.env` — delete it if present. Unlike `.env.example`, this
  contains a real (likely machine-specific) IP/port and possibly stale
  connection info; you'd be leaking the address of one of your test servers to
  the recipient. The recipient will create their own `.env` via
  `cp .env.example .env` anyway.
- `kio-simulator/__pycache__/` — delete it if present (compiled `.pyc` files,
  unnecessary, can be platform-specific).

Check: once you unpack the package, `remote-kio/` should only contain
`docker-compose.yml`, `.env.example`, `README.md`; `kio-simulator/` should
only contain `Dockerfile`, `envelope.py`, `kio_simulator.py`,
`requirements.txt` — nothing else.

### How it works

The architecture is already push-based (see the main `README.md`): every KIO
**pushes** its data to the central Collector over OTLP/gRPC. It doesn't matter
where the KIO runs — whether it's on the same machine or a different
continent, as long as it can reach the collector over the network. So
"moving it remote" requires no code changes at all, only one environment
variable (`OTEL_EXPORTER_OTLP_ENDPOINT`).

### Step-by-step setup

**1) Copy the two folders to the remote machine** (they must stay in the same
relative location, since this package uses `../kio-simulator` as its build
context — sharing a single source instead of duplicating code):

```bash
scp -r observability/kio-simulator observability/remote-kio user@remote-server:~/ai4sweng/
```

**2) Prepare the `.env` file on the remote machine:**

```bash
cd ~/ai4sweng/remote-kio
cp .env.example .env
```

At minimum, edit these in `.env`:

- `KIO_ID` — **must be unique** (see the warning below).
- `KIO_LLM`, `KIO_TASK_TYPE` — the model/task this KIO simulates.
- `OTEL_EXPORTER_OTLP_ENDPOINT` — the central machine's address, e.g.
  `http://192.168.1.50:4317` (same LAN) or a VPN/Tailscale IP. Must be
  **port 4317** (gRPC), not 4318 (HTTP) — the code uses the gRPC exporter.

There are two more optional blocks (already present as commented-out lines in
`.env.example`, off by default): `KIO_REAL_KPI_ROLE` (if this KIO represents
one of D1.1's six roles — bugfix/nlp-requirements/architecture-to-code/
ai-sysdev/green-deploy/adoption) and `NATS_ENABLED`/`NATS_URL` (if this KIO
should also be able to receive tasks from the central Workflow API — a
completely separate, optional concern from telemetry, see the "Orchestration
layer" section of the main README). Leave both alone if you don't need them.

**3) Verify connectivity** (before starting the KIO, make sure the central
machine's port 4317 is actually reachable):

```bash
nc -zv <central-machine-IP> 4317
```

If this fails, it's almost always either the central machine's **firewall
blocking 4317**, or the two machines **not being on the same network**. For
firewall-opening commands (Windows/Linux), finding the central machine's
address, same-LAN / static-IP / Tailscale options, and a more thorough
verification test (a real OTLP export), there's one stop:
**[`NETWORK.md`](NETWORK.md)**. In short: if you're on different networks,
Tailscale/ZeroTier is the simplest and safest fix — install it on both
machines, and put the `100.x.y.z` address you get from `tailscale ip -4` into
`OTEL_EXPORTER_OTLP_ENDPOINT` in `.env`; no static IP or port forwarding
needed.

**4) Run it:**

```bash
docker compose up -d --build
```

**5) Verify:** within ~30-60 seconds, the new `KIO_ID` should appear in the
`KIO` dropdown on Grafana's **KIO Detail** dashboard (allow time for the
heartbeat + metric export interval). `docker compose logs -f` also shows the
KIO's "online" log line.

### KIO_ID collision — important

If two sources (one local, one remote) send data under the same `kio_id` value
at the same time, the time series interleave and the dashboard looks
scrambled. Two options:

- **Use a new ID** (e.g. `kio5`) — no need to stop anything, the cleanest path.
- **Take over an existing ID** (e.g. `kio4`) — in this case, stop that KIO on
  the central machine first: `docker compose stop kio4` in the main
  `observability/` folder, then start the remote package.

### Security note

**As of 2026-08, a Bearer token is now mandatory** (§9.3 — the
`bearertokenauth` extension was added to the central collector, see
`otel-collector/config.yaml`). The `OTEL_EXPORTER_OTLP_HEADERS` line in `.env`
is no longer a comment — it's a required field to fill in (see
`.env.example`). Without the correct token, the collector accepts **none** of
this KIO's OTLP calls (metrics/logs/traces, both gRPC and HTTP); the KIO
itself won't crash, but its telemetry silently disappears (check the
container logs for connection errors). Get the token value from whoever runs
the central platform — it's one value shared across every KIO, not specific to
yours (it must match the `otel-collector` service's `OTLP_BEARER_TOKEN` in
`docker-compose.yml` exactly).

Beyond that, the connection is still **without TLS (insecure)** — real TLS is
still planned but not implemented for a setup that goes beyond a trusted
network (same LAN/VPN) to the open internet (see the main README's "V2
Guideline Evaluation"). For tests on the same LAN/VPN (Tailscale etc.), the
Bearer token alone is an adequate minimum precaution.

### Tearing down

```bash
docker compose down
```

No change is needed on the main stack; once this KIO stops, its
`kio_heartbeat` signal in Grafana cuts off after 120 seconds and the KIO
becomes "stale." This is now flagged automatically: the "Stale KIO" alert
rule in `grafana/provisioning/alerting/rules.yml` moves to "Firing" in Grafana
Alerting once that KIO_id has been silent for more than 120s; the "Stale KIO
Check" table on the Overview dashboard shows the same threshold visually (60s
orange / 120s red) — see the technical report's Section 9.5.
