#!/usr/bin/env python3
"""
A tiny demo KIO that pushes real telemetry to the AI4SWENG central platform.

It is NOT doing real work — it just loops, and on every tick it emits the seven
mandatory metrics (via a `kio.request(...)` block), a heartbeat (in the
background), and one log line. Point it at the platform, run it, and watch your
kio.id fill up the Grafana "KIO Detail" dashboard live.

    cp .env.example .env      # then edit KIO_ID + the endpoint/token
    pip install -r requirements.txt
    python main.py            # Ctrl-C to stop

Everything it sends is controlled by .env — no code changes needed. To send
telemetry from YOUR real module instead, copy kio_otel.py into your project and
wrap your own handler with `with kio.request(...)` (see the Integration Guide).
"""
import os
import random
import time


def _load_dotenv(path: str = ".env") -> None:
    """Load a local .env into os.environ (no dependency). Existing environment
    variables win, so `docker compose` / shell exports are not overridden."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()  # must run before importing kio_otel (it reads env at construction)

from kio_otel import KIOTelemetry  # noqa: E402


def do_one_request(req, kio) -> int:
    """Stand-in for real work. Replace this body with your actual logic; keep
    the record_tokens / kio.log calls (or drop them if you have no LLM)."""
    time.sleep(random.uniform(0.05, 0.4))            # pretend to do work
    in_tokens = random.randint(150, 1200)
    out_tokens = random.randint(80, 2000)
    req.record_tokens(input=in_tokens, output=out_tokens)
    kio.log(
        f"handled demo task on session {req.session_id[:8]} — {out_tokens} output tokens",
        **{"session.id": req.session_id},
    )
    return out_tokens


def main() -> None:
    interval = float(os.environ.get("REQUEST_INTERVAL_S", "3"))
    kio = KIOTelemetry()          # reads OTEL_* / KIO_* from the environment
    kio.start_heartbeat()
    print(f"[{kio.kio_id}] online — pushing telemetry every ~{interval:.0f}s. Ctrl-C to stop.")

    n = 0
    try:
        while True:
            n += 1
            with kio.request() as req:
                out = do_one_request(req, kio)
            print(f"[{kio.kio_id}] request #{n} sent  (metrics + log, {out} tokens)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n[{kio.kio_id}] stopping…")
    finally:
        kio.shutdown()            # flush the final batch before exit
        print(f"[{kio.kio_id}] flushed and exited.")


if __name__ == "__main__":
    main()
