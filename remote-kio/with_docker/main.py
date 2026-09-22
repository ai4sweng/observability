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
wrap your own handler with `with kio.request(...)`.

Langfuse (LLM prompt/completion/cost) is an OPTIONAL second stream, OFF by
default — the demo above works fully without it. It turns on only if you set
LANGFUSE_PUBLIC_KEY in .env (see the "Optional: Langfuse" note in README.md).
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


# --------------------------------------------------------------------------- #
# Optional Langfuse stream — OFF unless LANGFUSE_PUBLIC_KEY is set. Kept fully
# separate from the OTLP metrics/traces/logs above, and lazily imported so the
# `langfuse` package is only needed when you actually opt in.
# --------------------------------------------------------------------------- #
def _maybe_langfuse(kio_id: str):
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    # Langfuse lives on the SAME host as the collector, just a different port.
    # Derive its URL from the OTLP endpoint's IP + LANGFUSE_PORT so the address
    # is configured only once. An explicit LANGFUSE_HOST still wins.
    if not os.environ.get("LANGFUSE_HOST"):
        from urllib.parse import urlparse
        otlp = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:5317")
        host = urlparse(otlp if "://" in otlp else f"//{otlp}").hostname or "localhost"
        port = os.environ.get("LANGFUSE_PORT", "3001")
        os.environ["LANGFUSE_HOST"] = f"http://{host}:{port}"
    try:
        from langfuse import get_client
        client = get_client()  # reads LANGFUSE_PUBLIC_KEY / _SECRET_KEY / LANGFUSE_HOST
        print(f"[{kio_id}] Langfuse stream ON -> {os.environ['LANGFUSE_HOST']}")
        return client
    except Exception as exc:
        print(f"[{kio_id}] Langfuse keys set but stream OFF ({exc}). "
              f"Uncomment `langfuse` in requirements.txt and reinstall to enable.")
        return None


def _emit_langfuse(lf, session_id: str, model: str, in_tokens: int, out_tokens: int) -> None:
    if lf is None:
        return
    try:
        from langfuse import propagate_attributes
        with propagate_attributes(session_id=session_id, tags=["demo"]):
            with lf.start_as_current_observation(as_type="generation", name="llm_call", model=model) as gen:
                gen.update(
                    input="(demo prompt)",
                    output="(demo completion)",
                    usage_details={"input": in_tokens, "output": out_tokens},
                )
    except Exception:
        pass  # never let a Langfuse hiccup break the demo loop


def do_one_request(req, kio, lf, model) -> int:
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
    _emit_langfuse(lf, req.session_id, model, in_tokens, out_tokens)   # no-op if Langfuse off
    return out_tokens


def main() -> None:
    interval = float(os.environ.get("REQUEST_INTERVAL_S", "3"))
    model = os.environ.get("KIO_LLM", "demo-model")
    kio = KIOTelemetry()          # reads OTEL_* / KIO_* from the environment
    kio.start_heartbeat()
    lf = _maybe_langfuse(kio.kio_id)
    print(f"[{kio.kio_id}] online — pushing telemetry every ~{interval:.0f}s. Ctrl-C to stop.")

    n = 0
    try:
        while True:
            n += 1
            with kio.request() as req:
                out = do_one_request(req, kio, lf, model)
            extra = " + langfuse" if lf else ""
            print(f"[{kio.kio_id}] request #{n} sent  (metrics + log{extra}, {out} tokens)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n[{kio.kio_id}] stopping…")
    finally:
        if lf is not None:
            try:
                lf.shutdown()     # flush the Langfuse buffer
            except Exception:
                pass
        kio.shutdown()            # flush the OTLP batch before exit
        print(f"[{kio.kio_id}] flushed and exited.")


if __name__ == "__main__":
    main()
