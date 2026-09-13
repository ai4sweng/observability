# Real LLM Integration (KIO2, Optional — Requires an NVIDIA GPU)

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

Since KIO2's real module (FocusTracer) isn't connected yet, this path exists on
`kio2-sim` to at least **actually run a real LLM** and measure tokens/sec and
GPU energy for real in the meantime. Off by default (`KIO2_REAL_LLM_ENABLED`
defaults to `"false"` in `docker-compose.yml`) — since this path runs
independently of the FocusTracer connection, it can be turned on whenever
needed (e.g. for a demo) via `KIO2_REAL_LLM_ENABLED=true` in `.env` without
waiting for that handoff; it defaults off only because it requires a real
Ollama + GPU on the host (which a headless remote test server may not have).
What changes when it's on:

- **Real:** tokens/sec, input/output token counts, and latency (from Ollama's
  own `eval_count`/`eval_duration`), GPU energy AND GPU temperature (the
  integral of a real NVML reading over the call's duration / an instantaneous
  reading), **and now the error rate too** — if the Ollama call genuinely fails
  (unreachable/timeout/HTTP error), this is recorded as a real error with a
  real `error_type` on `kio.request.error_count`, not a random dice roll.
  Previously, even with the real path on, the error rate still came from the
  same 7% random dice roll, and a real Ollama outage would be papered over by
  a fresh fake "successful" request right after, as if nothing had happened —
  this is fixed (`_call_ollama_real()` now returns a dict that explicitly
  distinguishes success from failure; `simulate_request()` branches three ways:
  real-success / real-failure / path-fully-off).
- **Still simulated:** fix@1 (`kio.fix.attempt_count`, `outcome=success|failure`)
  — because judging a "correct fix" needs a real bug plus a real test run,
  which is FocusTracer's job. Once FocusTracer is ready, it will be enough to
  swap the body of `_evaluate_fix_success()` in `kio_simulator.py` — the metric
  name/shape stays the same. `kio.request.accuracy` (the general Contract
  metric, independent of D1.1/fix@1) also stays simulated for the same reason
  — there's no reference/test set to automatically score an LLM answer's
  "correctness."

## Setup (same steps on a Windows or Linux host)

1. Have Ollama installed on this machine (not inside a container) with the model already pulled: `ollama pull qwen2.5:3b`
2. (Optional, for real energy) `pip install nvidia-ml-py`, then run `python tools/power_exporter.py` — reads from NVML on the host (Windows or Linux) and serves JSON over `http://localhost:9400/power` (`{"watts": 87.3, "temperature_c": 61.5}` — `temperature_c` was added in 2026-08 for KIO Detail's GPU temperature gauge; silently omitted on older exporter/NVML versions that don't have it). If you skip this, energy falls back to the old estimated formula and the system still runs fine.
3. Add `KIO2_REAL_LLM_ENABLED=true` to a `.env` file at the repo root (start from `cp .env.example .env` if you don't have one), then restart with `docker compose up -d --build kio2-sim`; to turn it off again, delete the line or set it to `false` (the default is already `false`).

Why this approach (native Ollama on the host + a separate power-exporter
script, instead of GPU passthrough into the container)? Linux containers have
no visibility into the host's GPU unless passthrough is separately configured
(possible on Linux via `nvidia-container-toolkit`, but not used here); Ollama
already runs natively on the host (Windows or Linux, doesn't matter) and can
use the GPU directly, which is the lowest-friction path.
`_read_gpu_power_watts()` tries `GPU_POWER_EXPORTER_URL` first, then the
container's own NVML if available, and silently falls back to the old
estimated value if neither works — the simulator never crashes either way.

**Windows/Linux difference — `host.docker.internal`:** `OLLAMA_ENDPOINT` and
`GPU_POWER_EXPORTER_URL` reach the host from inside the container via this DNS
name. Docker Desktop (Windows/Mac) resolves it automatically; native Linux
Docker Engine (e.g. Docker installed on an Ubuntu server without Docker
Desktop) does not — which is why `docker-compose.yml` adds
`extra_hosts: ["host.docker.internal:host-gateway"]` to the `kio2-sim` service
(requires Docker Engine 20.10+). This line is harmless on Docker Desktop too,
resolving to the same address — a single `docker-compose.yml` works on both
Windows and Linux with no changes needed.
