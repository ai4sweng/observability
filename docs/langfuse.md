# Langfuse (LLM-Specific Tracing)

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

A second, parallel telemetry stream — independent of the OTel pipeline — dedicated
to prompt/completion/cost tracking. Each KIO wraps its simulated LLM call in a
Langfuse span+generation (same `session_id` as the OTel trace, so a human can
correlate the two), while metrics/logs/traces described in
[Metrics Reference & Query API](metrics-reference.md) keep flowing through OTel
exactly as before. If Langfuse is unreachable or misconfigured, the KIO logs a
warning and keeps running unaffected — this stream can never take down the rest
of the stack (see `kio_simulator.py`'s `emit_langfuse_trace()`).

Self-hosted via `LANGFUSE_INIT_*` "headless initialization" env vars on
`langfuse-web`, so the org/project/API-keys exist automatically on first boot —
no manual UI setup step, no copy-pasting keys before the KIOs can connect.

Login: `admin@ai4sweng.local` / `ai4sweng-admin` at **http://localhost:3001**,
auto-created on first boot. Langfuse takes noticeably longer to become ready
than the rest of the stack (~2–3 minutes: it's booting Postgres + ClickHouse +
Redis + MinIO underneath it) — the KIOs will log harmless connection-refused
retries against it until it's up, then start landing traces automatically.

## Remote / multi-machine deployments

**If you're running this on a remote Ubuntu server and connecting from a
different machine** (e.g. `docker compose up` runs on an Ubuntu server, but
the browser is on your Windows machine): `NEXTAUTH_URL` and
`LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` default to `localhost`, which only
resolves correctly from the machine Docker itself is running on. Create a
`.env` file at the repo root (copy from `.env.example`) and set
`PUBLIC_HOST=<the server's IP/hostname>` — otherwise Langfuse login (NextAuth
redirects to the wrong host) and multi-modal media previews (presigned URLs
pointing at the wrong host) break. Core OTel telemetry (Grafana/traces/
metrics/logs) and Langfuse's own trace/cost data are unaffected either way —
they already use Docker-internal addresses like `otel-collector`/`minio`. For
local use (Docker and the browser on the same machine, whichever OS — Windows
or Linux doesn't matter) you don't need to do anything, the default
`localhost` is already correct.

A remote **KIO** (rather than a remote browser) connecting its own telemetry
into Langfuse is covered in [Connecting a Remote KIO](remote-connectivity.md)
§9 ("Optional streams") of `remote-kio/INTEGRATION.md`.
