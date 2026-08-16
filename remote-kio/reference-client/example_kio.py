#!/usr/bin/env python3
"""
example_kio — a ~50-line runnable example of a REAL KIO using kio_otel.

This is NOT the internal simulator. It is the shape your own module takes:
you have some real work to do (call an LLM, analyze code, whatever), and you
wrap each unit of work in `with kio.request(...) as req:`. Replace the body of
`do_real_work()` with your actual logic and you are contract-compliant.

Run it (after `cp .env.example .env` and editing OTEL_EXPORTER_OTLP_ENDPOINT):

    pip install -r requirements.txt
    #  load .env into the environment, then:
    python example_kio.py

Within ~60s the KIO id you set appears in Grafana's "KIO Detail" dashboard.
Stop with Ctrl-C — the shutdown() call flushes the final batch cleanly.
"""
import os
import random
import time

from kio_otel import KIOTelemetry


def do_real_work(req):
    """Stand-in for YOUR actual request logic. Whatever your KIO really does
    goes here. The one thing that matters for telemetry: report the real LLM
    token usage (and cost, if any) back on `req`. Raising an exception here is
    fine — the context manager records it as a real error automatically."""
    time.sleep(random.uniform(0.1, 0.6))          # your real work takes real time
    input_tokens = random.randint(150, 1200)      # <- replace with your real counts
    output_tokens = random.randint(80, 2000)
    req.record_tokens(input=input_tokens, output=output_tokens)
    # req.record_cost_usd(0.0012)                 # only if your LLM has a $ cost
    return output_tokens


def main():
    kio = KIOTelemetry()          # reads OTEL_* / KIO_* from the environment
    kio.start_heartbeat()         # required by the contract (§2.3)
    kio_id = kio.kio_id
    print(f"[{kio_id}] online — Ctrl-C to stop")

    try:
        while True:
            # In a real KIO each "request" is a real incoming task; here we just
            # loop to produce a steady signal you can see land in Grafana.
            with kio.request() as req:
                out = do_real_work(req)
                print(f"[{kio_id}] handled a request ({out} output tokens)")
            time.sleep(float(os.environ.get("REQUEST_INTERVAL_S", "3")))
    except KeyboardInterrupt:
        print(f"\n[{kio_id}] stopping…")
    finally:
        kio.shutdown()            # flush the final batch before exit
        print(f"[{kio_id}] flushed and exited")


if __name__ == "__main__":
    main()
