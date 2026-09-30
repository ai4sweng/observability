# Langfuse Data Retention

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

Self-hosted OSS Langfuse keeps everything forever by default. Without the
measures below, its ClickHouse and MinIO volumes grow without bound (tens of
GB), while the OTel stores stay bounded by their own retention
(VictoriaMetrics/VictoriaLogs 30 days, Tempo 48 hours).

This page covers what now bounds Langfuse's disk use, the daily cleanup job,
the expected growth per KIO, and the emergency wipe procedure.

## Why Langfuse grew without bound

| Where | What piled up | Why |
|---|---|---|
| MinIO `langfuse/events/` | One raw JSON object per ingested event, kept forever | Langfuse writes every event to S3 before processing and never deletes it unless its data-retention job runs |
| ClickHouse `default.traces` / `observations` / `scores` | The processed traces the UI reads | Self-hosted Langfuse keeps data indefinitely by default |
| ClickHouse `system.*_log` | ClickHouse's own diagnostics (`trace_log`, `metric_log`, `asynchronous_metric_log`, `text_log`, …) | Written continuously even with no traffic; no TTL by default |
| ClickHouse `blob_storage_file_log` | One row per event file in MinIO | Bookkeeping for Langfuse's retention job |
| `/var/lib/docker/containers/*/*-json.log` | Container stdout | Docker's default `json-file` driver never rotates; the KIO simulators log one line per request |

Langfuse's built-in **Data Retention** setting (Project Settings → Data
Retention, or `LANGFUSE_INIT_PROJECT_RETENTION`) would handle the first two, but
on self-hosted instances it is an **Enterprise Edition** feature that needs a
license key. We run the OSS build, so each item above is handled separately.

## What bounds it now

| Mechanism | Where | Default | Change with |
|---|---|---|---|
| MinIO lifecycle rule: objects under `events/` expire | `langfuse/minio-entrypoint.sh`, re-applied on every `minio` start | 3 days | `LANGFUSE_EVENT_RETENTION_DAYS` |
| Daily cron deletes traces / observations / scores older than N days | `scripts/langfuse_retention.sh` | 30 days (same as VictoriaMetrics / VictoriaLogs) | `LANGFUSE_RETENTION_DAYS` (read by the script) |
| Unused ClickHouse system log tables disabled; `query_log` / `part_log` capped at 3 days, `error_log` at 7 | `langfuse/clickhouse/zz-ai4sweng-retention.xml` | — | edit the XML |
| ClickHouse server log files: `warning` level, 3 × 100 MB | same XML | — | edit the XML |
| `blob_storage_file_log` no longer written | `LANGFUSE_ENABLE_BLOB_STORAGE_FILE_LOG=false` on `langfuse-web` / `langfuse-worker` | off | — |
| Langfuse client-side sampling in the simulators | `LANGFUSE_SAMPLE_RATE` on `kio2-sim` / `kio7-sim` | 0.2 (20 % of requests) | `KIO_LANGFUSE_SAMPLE_RATE` |
| Docker log rotation for every container | `x-logging` anchor in `docker-compose.yml` | 3 × 10 MB | edit the anchor |
| Two simulators by default | `kio2-sim`, `kio7-sim` | — | [KIO Simulators](kio-simulators.md#why-only-two) |

The Docker log cap only applies to containers' stdout files (what `docker logs`
shows). When a file reaches 10 MB it is rotated and the oldest file is dropped,
so new lines are always written. Stored telemetry is not affected: it lives in
the Victoria/Tempo volumes under their own retention, and the KIO logs are also
sent to VictoriaLogs over OTLP.

Sampling only affects the Langfuse stream. OTel metrics, logs and traces are
still produced for every simulated request, so Grafana dashboards are
unchanged.

### Is dropping the raw MinIO events safe?

Yes, for this stack. The raw event is only needed until the worker has written
the ClickHouse rows (seconds), or if a *later* update arrives for the same
trace and has to be merged with it. Each simulated request opens and closes its
trace in one go, so a 3-day buffer is far more than needed. The Langfuse UI reads
from ClickHouse, not from these files.

Raw event storage cannot be turned off in Langfuse v3: the S3 upload is part of
the ingestion path. Expiring the files is the supported alternative. If a real
KIO ever sends long-lived traces (updates hours after the trace started), raise
`LANGFUSE_EVENT_RETENTION_DAYS`.

## Expected disk growth per KIO

Langfuse volume depends on how many traces reach it:

```
traces/day  = 86 400 / REQUEST_INTERVAL_S × LANGFUSE_SAMPLE_RATE
steady-state ClickHouse ≈ traces/day × LANGFUSE_RETENTION_DAYS       × bytes_per_trace_ch
steady-state MinIO      ≈ traces/day × LANGFUSE_EVENT_RETENTION_DAYS × bytes_per_trace_s3
```

Each simulated request becomes one Langfuse trace with two observations (the
`kio.request` span and the `llm_call` generation).

The byte sizes below are **planning assumptions**, not measurements: about
5 KB per trace in ClickHouse and 10 KB in MinIO (several small objects each, and
MinIO's per-object overhead dominates at this size). Replace them with measured
values once the server has run for a day (see [Measuring](#measuring-the-real-numbers)).

| KIO | Request interval | Traces/day (sample 0.2) | ClickHouse at 30 d | MinIO at 3 d |
|---|---|---|---|---|
| kio2-sim | 3 s | 5 760 | ≈ 860 MB | ≈ 170 MB |
| kio7-sim | 5 s | 3 456 | ≈ 520 MB | ≈ 100 MB |
| **Total** | | **9 216** | **≈ 1.4 GB** | **≈ 270 MB** |

For comparison, the old setup (six simulators, no sampling, no retention) sent
about 146 000 traces/day. At the same assumptions that is about 2 GB/day, and
it never stopped growing.

When capacity-planning a new KIO, use its real request rate. A **real** KIO
should normally not be sampled, so use a sample rate of 1.0 for it. Each
additional KIO at a 5 s interval, unsampled, adds about 17 000 traces/day,
roughly 2.6 GB in ClickHouse at 30 days and 500 MB in MinIO at 3 days.

## Setting up the cleanup job (remote server)

1. Deploy the change and recreate the containers, so the new config, log
   rotation and lifecycle rule take effect. `--remove-orphans` removes
   containers of services no longer in the compose file:

   ```bash
   cd /opt/observability   # wherever the repo lives on the server
   git pull
   docker compose up -d --build --remove-orphans
   ```

2. Check that MinIO applied the rule:

   ```bash
   docker logs ai4sweng-langfuse-minio 2>&1 | grep minio-entrypoint
   ```

   Expected output: `lifecycle rule applied — langfuse/events/* expire after 3 day(s)`.

3. Do a report-only run, then a real one:

   ```bash
   ./scripts/langfuse_retention.sh --report
   ./scripts/langfuse_retention.sh
   ```

4. Install the cron entry (daily, 03:00):

   ```bash
   ( crontab -l 2>/dev/null; echo '0 3 * * * cd /opt/observability && ./scripts/langfuse_retention.sh >> /var/log/langfuse-retention.log 2>&1' ) | crontab -
   ```

The script reads the ClickHouse and MinIO credentials from the containers' own
environment, so no secret goes into the crontab.

## Measuring the real numbers

`./scripts/langfuse_retention.sh --report` prints:

- the 15 largest ClickHouse tables (size and rows),
- traces per day for the last 7 days,
- the MinIO bucket size per prefix, and the active lifecycle rule.

To get `bytes_per_trace_ch`, divide the combined size of `traces` and
`observations` by the `traces` row count. For `bytes_per_trace_s3`, divide the
`events/` size by the traces ingested over the lifecycle window.

**Do not run `du` on the MinIO volume.** It walks every object file on the
host, which is what hung the server. Use `mc du` (the report does this) or
`df -h /` instead.

To see Docker's own log files:

```bash
du -sh /var/lib/docker/containers/*/*-json.log | sort -h | tail
```

## Emergency wipe (disk already full)

The Langfuse sub-stack is isolated from the OTel pipeline: wiping it loses
Langfuse trace history but does not touch Grafana, VictoriaMetrics,
VictoriaLogs or Tempo. On the next start, `langfuse-web`'s headless init
recreates the org, project, user and API keys from the compose environment,
so the KIOs reconnect without any manual step.

```bash
cd /opt/observability
docker compose stop langfuse-web langfuse-worker clickhouse minio postgres redis
docker compose rm -f langfuse-web langfuse-worker clickhouse minio postgres redis
docker volume rm observability_langfuse_clickhouse_data observability_langfuse_clickhouse_logs \
                 observability_langfuse_minio_data observability_langfuse_postgres_data \
                 observability_langfuse_redis_data
docker compose up -d --build --remove-orphans
```

Removing the MinIO volume can take a long time when it holds millions of files.
That is expected; let it finish.

If disk is not critical and you only want to trim, run
`LANGFUSE_RETENTION_DAYS=3 ./scripts/langfuse_retention.sh` instead. MinIO's
lifecycle scanner then clears expired `events/` objects over the following
hours.
