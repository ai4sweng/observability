#!/bin/sh
# Langfuse retention / disk report for the self-hosted (OSS) Langfuse sub-stack.
#
# Langfuse's built-in "Data Retention" setting is an Enterprise-Edition feature
# on self-hosted instances, so on our MIT build nothing is ever deleted. This
# script is the replacement, run from cron on the Docker host:
#
#   0 3 * * *  cd /opt/observability && ./scripts/langfuse_retention.sh >> /var/log/langfuse-retention.log 2>&1
#
# What it does (docs/data-retention.md has the full runbook):
#   1. Deletes Langfuse traces / observations / scores older than
#      LANGFUSE_RETENTION_DAYS (default 30, same as VictoriaMetrics/Logs) from ClickHouse.
#   2. Drops leftover system.*_N tables ClickHouse keeps when a system log's
#      schema/TTL changes (see langfuse/clickhouse/zz-ai4sweng-retention.xml).
#   3. Prints the per-table ClickHouse size and the MinIO bucket size.
#
# MinIO's raw event files are NOT handled here — the lifecycle rule set by
# langfuse/minio-entrypoint.sh expires them inside MinIO itself.
#
# Usage:
#   ./scripts/langfuse_retention.sh            # delete + report
#   ./scripts/langfuse_retention.sh --report   # report only, deletes nothing
set -eu

RETENTION_DAYS="${LANGFUSE_RETENTION_DAYS:-30}"
CH_CONTAINER="${CH_CONTAINER:-ai4sweng-langfuse-clickhouse}"
MINIO_CONTAINER="${MINIO_CONTAINER:-ai4sweng-langfuse-minio}"
REPORT_ONLY=false
[ "${1:-}" = "--report" ] && REPORT_ONLY=true

case "$RETENTION_DAYS" in
  ''|*[!0-9]*) echo "LANGFUSE_RETENTION_DAYS must be a whole number, got '$RETENTION_DAYS'" >&2; exit 2 ;;
esac

# Credentials are read from the container's own environment (set by
# docker-compose.yml), so nothing secret has to live in cron or this script.
ch() {
  docker exec -i "$CH_CONTAINER" sh -c \
    'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery'
}

echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') langfuse_retention (retention=${RETENTION_DAYS}d, report_only=${REPORT_ONLY})"

if [ "$REPORT_ONLY" = false ]; then
  # ALTER ... DELETE rewrites the affected parts, so disk space is actually
  # freed (a lightweight DELETE only masks rows until the next merge). Tables
  # that don't exist in the running Langfuse version are skipped.
  for spec in "traces:timestamp" "observations:start_time" "scores:timestamp"; do
    table="${spec%%:*}"
    col="${spec#*:}"
    exists=$(echo "EXISTS TABLE default.${table}" | ch)
    if [ "$exists" = "1" ]; then
      echo "ALTER TABLE default.${table} DELETE WHERE ${col} < now() - INTERVAL ${RETENTION_DAYS} DAY" | ch
      echo "queued delete: default.${table} (${col} older than ${RETENTION_DAYS}d)"
    fi
  done

  # system.query_log_0, system.part_log_1, ... — orphaned copies left behind
  # when the config override changed a system log table.
  echo "SELECT name FROM system.tables WHERE database = 'system' AND match(name, '_log_[0-9]+\$')" | ch |
  while read -r t; do
    [ -n "$t" ] || continue
    echo "DROP TABLE IF EXISTS system.\`${t}\` SYNC" | ch
    echo "dropped leftover system.${t}"
  done
fi

echo "--- ClickHouse size by table (top 15)"
ch <<'SQL'
SELECT database, table,
       formatReadableSize(sum(bytes_on_disk)) AS size,
       sum(rows) AS rows
FROM system.parts
WHERE active
GROUP BY database, table
ORDER BY sum(bytes_on_disk) DESC
LIMIT 15
FORMAT PrettyCompactMonoBlock
SQL

echo "--- Langfuse rows per day (last 7 days, traces)"
ch <<'SQL'
SELECT toDate(timestamp) AS day, count() AS traces
FROM default.traces
WHERE timestamp > now() - INTERVAL 7 DAY
GROUP BY day ORDER BY day
FORMAT PrettyCompactMonoBlock
SQL

echo "--- MinIO bucket size"
# `mc du` asks MinIO for the total instead of walking millions of files on the
# host filesystem, which is what made `du -sh` on the volume hang.
docker exec "$MINIO_CONTAINER" sh -c \
  'mc alias set lf http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc du --depth 2 lf/langfuse && mc ilm rule ls lf/langfuse' \
  || echo "(MinIO report failed)"
