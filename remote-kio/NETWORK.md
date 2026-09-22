# Network Setup — Connecting a Remote KIO to the Central Platform

This document explains how **a KIO running on a different machine** reaches
the central observability stack (OTel Collector → VictoriaMetrics/
VictoriaLogs/Tempo → Grafana) over the network. Both the "connecting your own
code" scenario (see [`INTEGRATION.md`](INTEGRATION.md)) and the "running our
simulator remotely" scenario (see [`README.md`](README.md)) use the exact same
network model — the only difference is which code runs; the networking side is
identical in both.

Throughout this document:
- **Central machine (B)** = the computer running the main `docker compose`
  (collector + databases + Grafana).
- **Remote machine** = the different computer running the KIO module.

---

## 1. The basic idea: push-based, one direction

The architecture is **push-based**. The KIO **pushes** its own telemetry to the
central collector; the central machine never opens a connection toward the
KIO, and never "scrapes" it. So the only thing required is:

> The remote machine must be able to open an **outbound** TCP connection
> toward the central machine's `:5317` (OTLP/gRPC).

The practical consequence: it doesn't matter where the KIO runs (same LAN, a
different city, behind NAT) — it works as long as it can reach the collector.
That's why "moving it remote" requires **no code change**, only one
environment variable (`OTEL_EXPORTER_OTLP_ENDPOINT`).

### Ports

| Port | Protocol | For | Used by |
|------|----------|---------|--------------|
| **5317** | OTLP/**gRPC** | Metrics + logs + traces | Every KIO in this repo (default) |
| 4318 | OTLP/**HTTP** | The same three signals, over HTTP transport | Clients that prefer HTTP over gRPC |
| 3000 | HTTP | Grafana UI | Anyone opening the dashboards remotely |
| 3001 | HTTP | Langfuse UI + API | (Optional) KIOs using the Langfuse stream |

> **Most common mistake:** setting the endpoint to `:4318`. The clients in
> this repo use **gRPC** → it must be port **5317**. 4318 is for HTTP, and if
> a gRPC client connects there you'll silently get "No data."

---

## 2. What needs to happen on the central machine (B) side

Good news: **the collector code is already ready for remote connections.**
The following two things are already in place:

1. The collector listens on both OTLP ports on all interfaces —
   `endpoint: 0.0.0.0:5317` / `0.0.0.0:4318` in `otel-collector/config.yaml`
   (not just `127.0.0.1`).
2. `docker-compose.yml` publishes these ports to the host (`"5317:5317"`,
   `"4318:4318"`) — Docker exposes them on `0.0.0.0` by default, i.e. reachable
   from the LAN.

Only one thing is left: **opening the inbound port on the firewall.** This is
the most common cause of "No data."

### 2a. Windows 11 (Docker Desktop) — firewall rule

If the central machine is Windows, open PowerShell as **Administrator**:

```powershell
New-NetFirewallRule -DisplayName "AI4SWENG OTLP gRPC" -Direction Inbound -LocalPort 5317 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "AI4SWENG OTLP HTTP" -Direction Inbound -LocalPort 4318 -Protocol TCP -Action Allow
```

If Grafana or Langfuse also need to be reachable from another machine (optional):

```powershell
New-NetFirewallRule -DisplayName "AI4SWENG Grafana"  -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "AI4SWENG Langfuse" -Direction Inbound -LocalPort 3001 -Protocol TCP -Action Allow
```

To remove a rule: `Remove-NetFirewallRule -DisplayName "AI4SWENG OTLP gRPC"`.

### 2b. Linux (native Docker Engine)

If you use `ufw`:

```bash
sudo ufw allow 5317/tcp
sudo ufw allow 4318/tcp
# optional: sudo ufw allow 3000/tcp ; sudo ufw allow 3001/tcp
```

If you use `firewalld`:

```bash
sudo firewall-cmd --permanent --add-port=5317/tcp --add-port=4318/tcp
sudo firewall-cmd --reload
```

### 2c. Finding the central machine's address

This is the address you'll put in the remote machine's
`OTEL_EXPORTER_OTLP_ENDPOINT`.

- **Windows:** `ipconfig` → "IPv4 Address" (e.g. `192.168.1.50`). Or:
  ```powershell
  (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -ne 'WellKnown' }).IPAddress
  ```
- **Linux:** `ip -4 addr show` or `hostname -I`.
- **If Tailscale is installed:** `tailscale ip -4` (e.g. `100.101.102.103` —
  this address never changes, see §3c).

---

## 3. Connection options — which one should I pick?

There are three paths. If you're on the same network, §3a is simplest; if
you're on different networks, §3c is recommended.

### 3a. Same LAN (same home/office network) — simplest

If both machines are on the same local network, no VPN is needed. On the
remote machine:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://<central-LAN-IP>:5317
```

> **Static IP recommendation:** LAN IPs can change over time via DHCP. If the
> central machine's IP changes on a reboot, every remote KIO drops to "No
> data." The cleanest fix is a **DHCP reservation** (fixed IP assignment) on
> your router, keyed to the central machine's MAC address — the address stays
> permanent and you never need to touch remote KIOs' `.env` files. Alternative:
> assign the central machine a static IP at the OS level.

### 3b. Static / public IP + port forwarding — be careful

If the machines are on different networks and you have a real static/public
IP, you can **forward** port `5317` to the central machine on your router
(port forwarding). But this **opens the port to the public internet**.

> ⚠️ This package listens **without authentication (insecure)** by default.
> Opening 5317 directly to the internet means anyone can send fake metrics.
> If you're opening it to the public internet, apply the TLS + Bearer token
> steps in §4 **first**. §3c (VPN) is generally safer and less work — it works
> without opening any port to the internet.

### 3c. Tailscale / ZeroTier (VPN) — recommended for different networks ✅

Tailscale (or ZeroTier) makes both machines behave as if they were **on the
same virtual network**. It gives every device a permanent, unchanging
`100.x.y.z` address even behind NAT/a router, with no port forwarding or
static/public IP purchase needed, and traffic is encrypted end to end.

**No need to worry about "same network," and no static IP needed either.**
That's the entire point of Tailscale.

Setup:
1. Install Tailscale on both machines and log into the same account/tailnet.
2. Find the address on the central machine: `tailscale ip -4` → e.g.
   `100.101.102.103`.
3. On the remote machine:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=http://100.101.102.103:5317
   ```
   (Since the Tailscale IP never changes, you won't need to update this again.
   Traffic inside Tailscale is already encrypted, so `http://` + insecure is
   sufficient just like on a LAN; the extra TLS in §4 isn't required.)

Two ways to add a KIO owner:
- **Full tailnet membership** ("Invite") — sensible if they're a teammate; you
  can limit which devices they can see with ACLs.
- **Single-device sharing** ("Share" — in the admin panel, next to your
  central machine) — shares just that one machine with an outside person
  without opening your whole tailnet to them. For a one-off/external
  recipient, this is the least-privilege path, and probably what you want.

> With Tailscale you may not even need to open a firewall port: traffic
> arrives via the `tailscale0` interface. If the connection still doesn't
> work, make sure the rule in §2 on the central machine also covers the
> Tailscale interface.

---

## 4. Security / TLS (production or internet-facing setups)

The default setup runs **without authentication**, adequate for tests within a
trusted network (same LAN or VPN) — just like the local setup. If you're
routing traffic over an untrusted network (§3b) or moving to production:

1. Add a `bearertokenauth` extension to the **central collector** and enable
   TLS on the OTLP receiver (`otel-collector/config.yaml`).
2. On the **remote KIO**, change the endpoint to `https://...:5317` and add
   the token:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=https://<central-address>:5317
   OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <TOKEN>
   ```
   The OpenTelemetry SDK reads both of these environment variables
   automatically — **no extra change is needed in the KIO's code** (the
   `https://` scheme turns TLS on, the header handles authorization).

This is the official/normative path from Integration Contract §1.2; the
local/LAN setup is its relaxed `insecure` variant.

---

## 5. Verification — can I actually connect?

In order:

**a) Is the port open? (quick TCP test)**

- If the remote machine is Windows:
  ```powershell
  Test-NetConnection <central-address> -Port 5317
  ```
  You should see `TcpTestSucceeded : True`.
- If the remote machine is Linux:
  ```bash
  nc -zv <central-address> 5317
  ```

**b) Does it flow end to end? (real export test)**

`with_script/check_connectivity.py` tries both the TCP connection and a
real OTLP export (a single `kio.heartbeat`), and tells you clearly where it
got stuck:

```bash
cd with_script
pip install -r requirements.txt
OTEL_EXPORTER_OTLP_ENDPOINT=http://<central-address>:5317 python check_connectivity.py
```

If it succeeds, `kio-preflight` (or whatever `KIO_ID` you gave it) appears in
Grafana → **KIO Detail** → the `KIO` dropdown within ~30-60 seconds.

---

## 6. Troubleshooting (quick table)

| Symptom | Likely cause | Fix |
|---|---|---|
| `check_connectivity.py` **[1/2] FAIL** | Wrong IP / firewall closed / different network | §2 (firewall), §2c (correct address), §3 (same network/VPN) |
| **[1/2] OK but [2/2] FAIL** | Port is open but the collector rejects the export (e.g. TLS/auth required) | §4 (TLS + token), or check the endpoint scheme (`http` vs `https`) |
| TCP succeeds, still "No data" in Grafana | Endpoint set to `:4318` (HTTP), client uses gRPC | Change the port to **5317** |
| Worked for a while, then dropped | The central machine's LAN IP changed via DHCP | §3a static IP / DHCP reservation |
| KIO appears but the series looks scrambled | Two sources sending the same `kio.id` | Use a unique `kio.id` (see INTEGRATION.md / README) |
| KIO marked "stale" | Heartbeat has been silent for >120s (KIO stopped or the network dropped) | Verify the KIO is up and exporting |

For detailed integration steps → [`INTEGRATION.md`](INTEGRATION.md).
