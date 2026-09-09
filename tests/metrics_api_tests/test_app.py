"""Endpoint tests for /api/metrics and /api/metrics/catalog.

Runs against a fake VictoriaMetrics (see conftest.py) — no live stack, no
Docker, matching the repo's existing convention of faking every external
dependency.
"""
import pytest

from metrics_api.victoriametrics import UpstreamQueryError, UpstreamUnavailable

from .conftest import matrix, vector


class TestHealth:
    def test_health_reports_registry_size(self, api):
        body = api.get("/health").json()
        assert body["status"] == "ok"
        assert body["metrics_in_registry"] == 33


class TestCatalog:
    def test_defaults_to_the_operational_family(self, api):
        body = api.get("/api/metrics/catalog").json()
        assert body["family"] == "operational"
        assert body["count"] == 14
        assert {m["family"] for m in body["metrics"]} == {"operational"}

    def test_entries_carry_what_a_caller_needs_to_query_them(self, api):
        body = api.get("/api/metrics/catalog").json()
        entry = next(m for m in body["metrics"] if m["otel_name"] == "kio.request.count")
        assert entry["prom_name"] == "kio_request_count"
        assert entry["instrument"] == "counter"
        assert entry["unit"] == "1"
        assert entry["default_aggregate"] == "rate"
        assert "status" in entry["required_group_by"]
        assert "source" in entry["valid_labels"]

    def test_metrics_without_a_source_label_say_so(self, api):
        body = api.get("/api/metrics/catalog").json()
        heartbeat = next(m for m in body["metrics"] if m["otel_name"] == "kio.heartbeat")
        assert "source" not in heartbeat["valid_labels"]

    def test_kpi_family_is_available_but_not_the_default(self, api):
        assert api.get("/api/metrics/catalog?family=kpi").json()["count"] == 19
        assert api.get("/api/metrics/catalog?family=all").json()["count"] == 33

    def test_unknown_family_is_rejected(self, api):
        assert api.get("/api/metrics/catalog?family=bogus").status_code == 422


class TestSnapshot:
    def test_returns_the_value_with_unit_and_instrument(self, api, fake_vm):
        fake_vm.instant = [vector(1843.2)]
        body = api.get("/api/metrics?metric=request_duration_ms&kio_id=kio2-sim").json()
        result = body["results"][0]
        assert body["format"] == "snapshot"
        assert result["value"] == 1843.2
        assert result["unit"] == "ms"
        assert result["instrument"] == "histogram"
        assert result["aggregate"] == "avg"

    def test_generates_the_histogram_ratio_query(self, api, fake_vm):
        fake_vm.instant = [vector(1.0)]
        api.get("/api/metrics?metric=request_duration_ms&kio_id=kio2-sim")
        assert fake_vm.last_query == (
            'sum (rate(kio_request_duration_ms_sum{kio_id="kio2-sim"}[5m])) '
            '/ clamp_min(sum (rate(kio_request_duration_ms_count{kio_id="kio2-sim"}[5m])), 0.001)'
        )

    def test_counter_defaults_to_a_rate(self, api, fake_vm):
        fake_vm.instant = [vector(4.2)]
        api.get("/api/metrics?metric=request_count")
        assert fake_vm.last_query == "sum (rate(kio_request_count[5m]))"

    def test_several_metrics_in_one_request(self, api, fake_vm):
        fake_vm.instant = [vector(1.0)]
        body = api.get("/api/metrics?metric=request_count,heartbeat").json()
        assert [r["metric"] for r in body["results"]] == ["kio.request.count", "kio.heartbeat"]

    def test_group_by_returns_one_entry_per_series(self, api, fake_vm):
        fake_vm.instant = [vector(3.0, status="ok"), vector(0.2, status="error")]
        body = api.get("/api/metrics?metric=request_count&group_by=status").json()
        groups = body["results"][0]["groups"]
        assert [g["labels"]["status"] for g in groups] == ["ok", "error"]
        assert "value" not in body["results"][0]

    def test_the_generated_query_is_echoed_back(self, api, fake_vm):
        # Makes the abstraction inspectable: a caller can see what PromQL ran.
        fake_vm.instant = [vector(1.0)]
        body = api.get("/api/metrics?metric=heartbeat").json()
        assert body["results"][0]["query"] == "sum (rate(kio_heartbeat[5m]))"


class TestNoData:
    """An empty result must always explain itself — the whole point is that
    "expected absence" and "something is broken" stop looking alike."""

    def test_conditional_kpi_metric_explains_its_role_gate(self, api, fake_vm):
        fake_vm.instant = []
        body = api.get("/api/metrics?metric=kio.bugfix.duration_hours").json()
        reason = body["results"][0]["no_data_reason"]
        assert "KIO_REAL_KPI_ROLE=bugfix" in reason
        assert body["results"][0]["value"] is None

    def test_repo_gauge_explains_its_task_type_gate(self, api, fake_vm):
        fake_vm.instant = []
        body = api.get("/api/metrics?metric=repo_line_count").json()
        assert "code-analysis" in body["results"][0]["no_data_reason"]

    def test_core_metric_suggests_checking_the_pipeline(self, api, fake_vm):
        fake_vm.instant = []
        body = api.get("/api/metrics?metric=request_count").json()
        assert "collector" in body["results"][0]["no_data_reason"]

    def test_no_data_is_a_200_not_an_error(self, api, fake_vm):
        fake_vm.instant = []
        assert api.get("/api/metrics?metric=request_count").status_code == 200


class TestSummary:
    def test_summary_computes_statistics_from_one_range_query(self, api, fake_vm):
        fake_vm.range_ = [matrix([10.0, 20.0, 30.0, 40.0])]
        body = api.get("/api/metrics?metric=request_duration_ms&period=24h&format=summary").json()
        analysis = body["results"][0]["analysis"]
        assert analysis["min"] == 10.0
        assert analysis["max"] == 40.0
        assert analysis["avg"] == 25.0
        assert analysis["current"] == 40.0
        assert analysis["samples"] == 4
        # One upstream call, not one per statistic.
        assert len(fake_vm.range_calls) == 1

    def test_relative_window_is_passed_to_victoriametrics(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=24h&format=summary")
        call = fake_vm.range_calls[0]
        assert call["start"] == "-24h"
        assert call["end"] == "now"

    def test_auto_step_is_period_over_100(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=24h&format=summary")
        assert fake_vm.range_calls[0]["step"] == "864s"

    def test_explicit_step_is_honoured(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=24h&step=1h&format=summary")
        assert fake_vm.range_calls[0]["step"] == "1h"

    def test_nan_samples_are_ignored_rather_than_poisoning_the_stats(self, api, fake_vm):
        fake_vm.range_ = [matrix([10.0, None, 30.0])]
        analysis = api.get(
            "/api/metrics?metric=request_count&period=1h&format=summary"
        ).json()["results"][0]["analysis"]
        assert analysis["samples"] == 2
        assert analysis["avg"] == 20.0


class TestSeries:
    def test_series_returns_the_points(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0, 2.0], kio_id="kio2-sim")]
        body = api.get("/api/metrics?metric=request_count&period=1h&format=series").json()
        series = body["results"][0]["series"][0]
        assert series["labels"] == {"kio_id": "kio2-sim"}
        assert [p["value"] for p in series["points"]] == [1.0, 2.0]


class TestFormatResolution:
    def test_auto_without_a_range_is_a_snapshot(self, api, fake_vm):
        fake_vm.instant = [vector(1.0)]
        assert api.get("/api/metrics?metric=request_count").json()["format"] == "snapshot"

    def test_auto_with_absolute_range_is_a_summary(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        body = api.get(
            "/api/metrics?metric=request_count"
            "&start=2026-09-08T00:00:00Z&end=2026-09-09T00:00:00Z"
        ).json()
        assert body["format"] == "summary"
        assert body["start"] == "2026-09-08T00:00:00Z"
        assert "period" not in body

    def test_unknown_format_is_rejected(self, api):
        response = api.get("/api/metrics?metric=request_count&format=chart")
        assert response.status_code == 400
        assert "valid:" in response.json()["error"]


class TestValidation:
    def test_metric_is_required(self, api):
        assert api.get("/api/metrics").status_code == 422

    def test_unknown_metric_returns_400_with_suggestions(self, api):
        response = api.get("/api/metrics?metric=duration_hours")
        assert response.status_code == 400
        assert "kio.bugfix.duration_hours" in response.json()["error"]

    def test_filtering_by_a_label_the_metric_lacks_returns_400(self, api):
        # Not an empty 200 — that would look like a broken pipeline.
        response = api.get("/api/metrics?metric=heartbeat&source=real")
        assert response.status_code == 400
        assert "does not carry label 'source'" in response.json()["error"]

    def test_unknown_query_parameter_is_not_silently_ignored(self, api):
        # A dropped filter would return real-looking numbers for a query the
        # caller never asked for.
        response = api.get("/api/metrics?metric=request_count&kioid=kio2-sim")
        assert response.status_code == 400
        assert "kioid" in response.json()["error"]

    def test_averaging_a_counter_returns_400(self, api):
        response = api.get("/api/metrics?metric=request_count&aggregate=avg")
        assert response.status_code == 400
        assert "monotonic counter" in response.json()["error"]

    def test_period_and_absolute_range_together_returns_400(self, api):
        response = api.get(
            "/api/metrics?metric=request_count&period=24h"
            "&start=2026-09-08T00:00:00Z&end=2026-09-09T00:00:00Z"
        )
        assert response.status_code == 400
        assert "mutually exclusive" in response.json()["error"]

    def test_start_without_end_returns_400(self, api):
        response = api.get("/api/metrics?metric=request_count&start=2026-09-08T00:00:00Z")
        assert response.status_code == 400
        assert "together" in response.json()["error"]

    def test_malformed_period_returns_400(self, api):
        response = api.get("/api/metrics?metric=request_count&period=24hours")
        assert response.status_code == 400
        assert "duration" in response.json()["error"]

    def test_too_many_metrics_is_refused(self, api):
        names = ",".join(
            [
                "request_count", "request_error_count", "request_duration_ms",
                "llm_token_count", "llm_cost_usd", "llm_energy_joules",
                "llm_tokens_per_second", "request_accuracy", "heartbeat",
                "session_active_count", "repo_line_count",
            ]
        )
        response = api.get(f"/api/metrics?metric={names}")
        assert response.status_code == 400
        assert "limit is 10" in response.json()["error"]

    def test_label_value_cannot_inject_into_the_query(self, api):
        response = api.get('/api/metrics?metric=request_count&kio_id=x"} or up{')
        assert response.status_code == 400
        assert "invalid value" in response.json()["error"]


class TestUpstreamFailures:
    """Unreachable and rejected must not look like each other, or like no data."""

    def test_unreachable_victoriametrics_is_a_502(self, api, fake_vm):
        fake_vm.raises = UpstreamUnavailable("could not reach VictoriaMetrics at http://vm:8428")
        response = api.get("/api/metrics?metric=request_count")
        assert response.status_code == 502
        assert "could not reach" in response.json()["error"]

    def test_rejected_query_is_a_400(self, api, fake_vm):
        fake_vm.raises = UpstreamQueryError("unexpected token")
        response = api.get("/api/metrics?metric=request_count")
        assert response.status_code == 400
        assert "unexpected token" in response.json()["error"]


class TestTimeIntervalParameters:
    """`period`, `start`/`end`, `step`, `window`, `tz` and `align` — the full
    set of time controls, as seen from the endpoint."""

    def test_relative_period_stays_relative_when_unaligned(self, api, fake_vm):
        # No clock arithmetic needed: VictoriaMetrics understands -6h/now.
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=6h&step=5m&format=series")
        call = fake_vm.range_calls[0]
        assert (call["start"], call["end"]) == ("-6h", "now")

    def test_absolute_range_is_passed_through_when_unaligned(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get(
            "/api/metrics?metric=request_count&format=series&step=5m"
            "&start=2026-09-08T00:00:00Z&end=2026-09-09T00:00:00Z"
        )
        call = fake_vm.range_calls[0]
        assert call["start"] == "2026-09-08T00:00:00Z"

    def test_daily_step_is_aligned_to_midnight(self, api, fake_vm):
        # Issue example: period=7d&step=1d should produce midnight buckets,
        # not points offset from whenever the request happened to be made.
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=7d&step=1d&format=series")
        start = float(fake_vm.range_calls[0]["start"])
        assert start % 86400 == 0, "daily bucket did not land on a UTC midnight"

    def test_timezone_moves_the_daily_boundary(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=7d&step=1d&format=series")
        utc_start = float(fake_vm.range_calls[0]["start"])

        fake_vm.range_calls.clear()
        api.get(
            "/api/metrics?metric=request_count&period=7d&step=1d&format=series"
            "&tz=Europe/Istanbul"
        )
        istanbul_start = float(fake_vm.range_calls[0]["start"])
        # Istanbul is UTC+3, so its local midnight is 3h before UTC midnight.
        assert utc_start - istanbul_start == 3 * 3600

    def test_timezone_is_echoed_in_the_response(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        body = api.get(
            "/api/metrics?metric=request_count&period=7d&step=1d&format=series"
            "&tz=Europe/Istanbul"
        ).json()
        assert body["timezone"] == "Europe/Istanbul"

    def test_align_none_keeps_a_daily_step_relative(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get(
            "/api/metrics?metric=request_count&period=7d&step=1d&format=series&align=none"
        )
        assert fake_vm.range_calls[0]["start"] == "-7d"

    def test_align_calendar_forces_alignment_of_a_small_step(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get(
            "/api/metrics?metric=request_count&period=1h&step=5m&format=series"
            "&align=calendar"
        )
        start = float(fake_vm.range_calls[0]["start"])
        assert start % 300 == 0

    def test_sub_hourly_step_is_not_aligned_under_auto(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        api.get("/api/metrics?metric=request_count&period=1h&step=5m&format=series")
        assert fake_vm.range_calls[0]["start"] == "-1h"

    def test_unknown_timezone_returns_400(self, api, fake_vm):
        response = api.get(
            "/api/metrics?metric=request_count&period=7d&step=1d&format=series"
            "&tz=Mars/Olympus_Mons"
        )
        assert response.status_code == 400
        assert "IANA" in response.json()["error"]

    def test_unknown_align_returns_400(self, api, fake_vm):
        response = api.get(
            "/api/metrics?metric=request_count&period=1h&format=series&align=maybe"
        )
        assert response.status_code == 400
        assert "valid:" in response.json()["error"]

    def test_window_is_echoed_and_derived_from_step(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        body = api.get(
            "/api/metrics?metric=request_count&period=1h&step=5m&format=series"
        ).json()
        assert body["window"] == "315s"           # 300s step + 15s export interval
        assert "[315s]" in body["results"][0]["query"]

    def test_explicit_window_overrides_the_derived_one(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0])]
        body = api.get(
            "/api/metrics?metric=request_count&period=24h&step=1h&window=3m&format=series"
        ).json()
        assert body["window"] == "3m"
        assert "[3m]" in body["results"][0]["query"]

    def test_snapshot_window_is_the_period(self, api, fake_vm):
        fake_vm.instant = [vector(1.0)]
        body = api.get("/api/metrics?metric=request_count&period=15m").json()
        assert body["window"] == "15m"


class TestTimestampFormat:
    def test_points_carry_both_iso_and_unix_timestamps(self, api, fake_vm):
        fake_vm.range_ = [matrix([1.0, 2.0], start=1_757_000_000.0, step=60.0)]
        body = api.get(
            "/api/metrics?metric=request_count&period=1h&step=5m&format=series"
        ).json()
        points = body["results"][0]["series"][0]["points"]
        assert points[0]["timestamp"] == "2025-09-04T15:33:20Z"
        assert points[0]["unix"] == 1_757_000_000.0
        assert points[1]["timestamp"] == "2025-09-04T15:34:20Z"
