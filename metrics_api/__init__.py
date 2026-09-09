"""Metrics query API — a read-only, parameterized front end over the
telemetry the KIOs push into VictoriaMetrics.

Callers get to ask for `kio.request.duration_ms` over the last hour without
knowing that VictoriaMetrics stores it as `kio_request_duration_ms`, that it is
a histogram whose average is `rate(_sum) / rate(_count)`, or that
`kio.request.count` carries a `status` label that must be summed over or the
answer is double-counted.

Ships unauthenticated by deliberate decision: this deployment is for testing
and carries no sensitive data (the telemetry is simulated KIO output), so the
endpoint is open in order to be usable without friction. It matches the
posture of the VictoriaMetrics/VictoriaLogs/Tempo read paths beside it, which
are also open. Revisit if real data ever flows through this stack.
"""
