#!/bin/sh
# MinIO entrypoint for the Langfuse sub-stack (mounted by docker-compose.yml).
#
# Starts `minio server` as before, then (re)applies a bucket lifecycle rule
# that expires Langfuse's raw ingestion event files under events/ after
# LANGFUSE_EVENT_RETENTION_DAYS days (default 3).
#
# Why this is safe: Langfuse writes every ingested event to S3 first, the
# worker reads it back to build the ClickHouse rows, and after that the file
# is only needed if a *later* update arrives for the same trace. The KIO
# simulators finish each trace within a single request, so a few days of
# buffer is plenty. The processed copy in ClickHouse is what the Langfuse UI
# reads — it is not touched by this rule (see docs/data-retention.md for the
# ClickHouse side).
#
# `mc ilm import` replaces the bucket's whole lifecycle config, so re-running
# this on every start is idempotent and also restores the rule after a volume
# wipe.
set -eu

RETENTION_DAYS="${LANGFUSE_EVENT_RETENTION_DAYS:-3}"

mkdir -p /data/langfuse
minio server --address ":9000" --console-address ":9001" /data &
MINIO_PID=$!
trap 'kill -TERM "$MINIO_PID" 2>/dev/null' TERM INT

i=0
until mc alias set lf http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "minio-entrypoint: MinIO not reachable after 60s, lifecycle rule NOT applied" >&2
    break
  fi
  sleep 1
done

if [ "$i" -lt 60 ]; then
  printf '{"Rules":[{"ID":"expire-langfuse-events","Status":"Enabled","Filter":{"Prefix":"events/"},"Expiration":{"Days":%s}}]}' "$RETENTION_DAYS" \
    | mc ilm import lf/langfuse \
    && echo "minio-entrypoint: lifecycle rule applied — langfuse/events/* expire after ${RETENTION_DAYS} day(s)" \
    || echo "minio-entrypoint: FAILED to apply lifecycle rule" >&2
fi

wait "$MINIO_PID"
