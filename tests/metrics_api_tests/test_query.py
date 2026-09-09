"""Unit tests for PromQL generation.

The assertions here are deliberately about the exact query text. A wrong query
shape does not raise — it returns a plausible-looking number that is silently
meaningless (an average of a monotonic counter, a double-counted token rate),
which is exactly the class of bug this API is meant to remove.
"""
import pytest

from metrics_api import query, registry


def q(metric_name, **kwargs):
    return query.build(registry.resolve(metric_name), **kwargs)


class TestHistograms:
    def test_average_uses_the_guarded_sum_over_count_idiom(self):
        assert q("request_duration_ms", filters={"kio_id": "kio2-sim"}) == (
            'sum (rate(kio_request_duration_ms_sum{kio_id="kio2-sim"}[5m])) '
            '/ clamp_min(sum (rate(kio_request_duration_ms_count{kio_id="kio2-sim"}[5m])), 0.001)'
        )

    def test_p95_uses_histogram_quantile_over_buckets(self):
        assert q("request_duration_ms", aggregate="p95") == (
            "histogram_quantile(0.95, sum by (le) (rate(kio_request_duration_ms_bucket[5m])))"
        )

    def test_p99_is_supported(self):
        assert "histogram_quantile(0.99" in q("request_duration_ms", aggregate="p99")

    def test_quantile_keeps_le_alongside_a_group_by(self):
        # Dropping `le` in the inner aggregation would leave histogram_quantile
        # with nothing to interpolate over.
        expr = q("request_duration_ms", aggregate="p95", group_by=["kio_id"])
        assert "sum by (kio_id,le)" in expr

    def test_sum_totals_the_observed_values(self):
        assert q("request_duration_ms", aggregate="sum") == (
            "sum (increase(kio_request_duration_ms_sum[5m]))"
        )

    def test_rate_counts_observation_frequency_not_magnitude(self):
        assert q("request_duration_ms", aggregate="rate") == (
            "sum (rate(kio_request_duration_ms_count[5m]))"
        )

    def test_default_aggregate_is_average(self):
        assert query.default_aggregate(registry.resolve("request_duration_ms")) == "avg"


class TestCounters:
    def test_default_is_a_rate_not_an_average(self):
        assert q("request_count") == "sum (rate(kio_request_count[5m]))"
        assert query.default_aggregate(registry.resolve("request_count")) == "rate"

    def test_increase_is_supported(self):
        assert q("request_count", aggregate="increase") == (
            "sum (increase(kio_request_count[5m]))"
        )

    def test_sum_gives_the_raw_cumulative_total(self):
        assert q("llm_token_count", aggregate="sum") == "sum (kio_llm_token_count)"

    @pytest.mark.parametrize("aggregate", ["avg", "min", "max"])
    def test_averaging_a_monotonic_counter_is_refused(self, aggregate):
        # The original single-global-default design would have silently
        # answered this with a meaningless number.
        with pytest.raises(query.InvalidQuery) as excinfo:
            q("request_count", aggregate=aggregate)
        assert "monotonic counter" in str(excinfo.value)


class TestGauges:
    def test_gauge_is_read_directly_with_no_rate(self):
        assert q("repo_line_count") == "sum (kio_repo_line_count)"
        assert "rate(" not in q("repo_line_count")

    def test_up_down_counter_is_treated_as_a_gauge(self):
        assert q("session_active_count") == "sum (kio_session_active_count)"

    def test_avg_min_max_are_available(self):
        assert q("repo_line_count", aggregate="avg").startswith("avg (")
        assert q("repo_line_count", aggregate="max").startswith("max (")

    def test_rate_on_a_gauge_is_refused(self):
        with pytest.raises(query.InvalidQuery):
            q("repo_line_count", aggregate="rate")


class TestSelectors:
    def test_no_filters_produces_no_selector(self):
        assert q("heartbeat") == "sum (rate(kio_heartbeat[5m]))"

    def test_single_value_uses_equality(self):
        assert 'kio_id="kio2-sim"' in q("heartbeat", filters={"kio_id": "kio2-sim"})

    def test_comma_separated_values_become_one_regex(self):
        # One query for several KIOs rather than N round trips.
        assert 'kio_id=~"kio2-sim|kio3"' in q("heartbeat", filters={"kio_id": "kio2-sim,kio3"})

    def test_a_list_is_accepted_as_well_as_a_string(self):
        assert 'kio_id=~"kio2-sim|kio3"' in q("heartbeat", filters={"kio_id": ["kio2-sim", "kio3"]})

    def test_labels_are_emitted_in_a_stable_order(self):
        # Otherwise the generated query text churns and caching suffers.
        expr = q("request_count", filters={"source": "real", "kio_id": "kio2-sim", "llm": "qwen2.5:3b"})
        assert expr.index("kio_id=") < expr.index("llm=") < expr.index("source=")

    def test_empty_filter_values_are_dropped(self):
        assert q("heartbeat", filters={"kio_id": ""}) == "sum (rate(kio_heartbeat[5m]))"

    def test_label_a_metric_does_not_carry_is_refused(self):
        with pytest.raises(registry.InvalidLabel):
            q("heartbeat", filters={"source": "real"})

    @pytest.mark.parametrize("hostile", ['kio2"} or up{', "kio2,}", 'a"b', "a{b}"])
    def test_label_values_cannot_reshape_the_query(self, hostile):
        with pytest.raises(query.InvalidQuery):
            q("request_count", filters={"kio_id": hostile})

    def test_llm_values_with_dots_and_colons_are_allowed(self):
        # Real LLM identifiers look like "qwen2.5:3b".
        assert 'llm="qwen2.5:3b"' in q("request_count", filters={"llm": "qwen2.5:3b"})


class TestGrouping:
    def test_default_aggregates_away_extra_dimensions(self):
        # kio.request.count carries status=ok|error; without the bare `sum`
        # this returns two series instead of one request rate.
        assert q("request_count") == "sum (rate(kio_request_count[5m]))"

    def test_group_by_breaks_out_a_dimension_on_request(self):
        assert q("request_count", group_by=["status"]) == (
            "sum by (status) (rate(kio_request_count[5m]))"
        )

    def test_token_count_defaults_to_summing_over_direction(self):
        # Otherwise input and output tokens are double-counted as throughput.
        assert q("llm_token_count") == "sum (rate(kio_llm_token_count[5m]))"

    def test_group_by_multiple_dimensions(self):
        assert "sum by (kio_id,status)" in q("request_count", group_by=["kio_id", "status"])

    def test_group_by_an_unavailable_label_is_refused(self):
        with pytest.raises(query.InvalidQuery) as excinfo:
            q("heartbeat", group_by=["source"])
        assert "available labels" in str(excinfo.value)


class TestPeriodAndStep:
    def test_period_is_interpolated_into_the_window(self):
        assert "[24h]" in q("request_count", period="24h")

    @pytest.mark.parametrize("bad", ["24", "24 h", "hour", "", "1x", "-5m"])
    def test_malformed_period_is_refused(self, bad):
        with pytest.raises(query.InvalidQuery):
            q("request_count", period=bad)

    @pytest.mark.parametrize(
        "period,expected",
        [
            ("24h", "864s"),    # 86400 / 100
            ("7d", "6048s"),
            ("5m", "15s"),      # floored at the 15s export interval
            ("1m", "15s"),
        ],
    )
    def test_auto_step_is_period_over_100_floored_at_the_export_interval(self, period, expected):
        assert query.auto_step(period) == expected

    @pytest.mark.parametrize(
        "value,seconds",
        [("15s", 15), ("5m", 300), ("24h", 86400), ("7d", 604800), ("2w", 1209600)],
    )
    def test_duration_seconds(self, value, seconds):
        assert query.duration_seconds(value) == seconds


class TestAggregateValidation:
    def test_unknown_aggregate_lists_the_valid_ones(self):
        with pytest.raises(query.InvalidQuery) as excinfo:
            q("request_duration_ms", aggregate="median")
        assert "valid:" in str(excinfo.value)

    def test_aggregate_is_case_insensitive(self):
        assert q("request_duration_ms", aggregate="P95") == q(
            "request_duration_ms", aggregate="p95"
        )


class TestRateWindow:
    """`window` is the rate lookback per point; `period` is the span asked
    about. Conflating them is a real defect: evaluating rate(x[24h]) at every
    step of a 24h range returns a 24h-smoothed value at each point, so the
    series flattens and min/max collapse toward the mean.
    """

    def test_window_defaults_to_period(self):
        # Correct for an instant query: one number over one window.
        assert "[5m]" in q("request_count")
        assert "[24h]" in q("request_count", period="24h")

    def test_explicit_window_overrides_period(self):
        expr = q("request_count", period="24h", window="2m")
        assert "[2m]" in expr
        assert "[24h]" not in expr

    def test_window_applies_to_histograms_too(self):
        expr = q("request_duration_ms", period="24h", window="2m")
        assert "[2m]" in expr
        assert "[24h]" not in expr

    def test_window_applies_to_quantiles(self):
        expr = q("request_duration_ms", period="7d", window="5m", aggregate="p95")
        assert "_bucket[5m]" in expr

    def test_window_is_irrelevant_to_gauges(self):
        # A gauge has no rate window at all.
        assert q("repo_line_count", period="24h", window="1m") == "sum (kio_repo_line_count)"

    @pytest.mark.parametrize("bad", ["2", "2 m", "abc", "5 minutes"])
    def test_malformed_window_is_refused(self, bad):
        with pytest.raises(query.InvalidQuery) as excinfo:
            q("request_count", window=bad)
        assert "window" in str(excinfo.value)

    def test_empty_window_means_unset_rather_than_invalid(self):
        # Consistent with empty label filters, which are dropped rather than
        # rejected — `?window=` reads as "no preference".
        assert q("request_count", period="1h", window="") == q(
            "request_count", period="1h"
        )

    @pytest.mark.parametrize(
        "step,expected",
        [
            ("36s", "60s"),        # floor: 4 x the 15s export interval
            ("15s", "60s"),
            ("5s", "60s"),
            ("2m", "135s"),        # step + one export interval
            ("864s", "879s"),
            ("1h", "3615s"),
            ("1d", "86415s"),      # ~= one day, NOT four
            ("30d", "2592015s"),   # ~= 30 days, NOT 120
        ],
    )
    def test_auto_window_tracks_the_step_with_a_floor(self, step, expected):
        assert query.auto_window(step) == expected

    def test_auto_window_never_narrower_than_a_few_export_intervals(self):
        # Metrics export every 15s; a window under ~60s straddles too few
        # samples and produces gaps rather than a rate.
        assert query.duration_seconds(query.auto_window("15s")) >= 60

    @pytest.mark.parametrize("step", ["2m", "1h", "1d", "7d", "30d"])
    def test_auto_window_never_smooths_across_neighbouring_buckets(self, step):
        # The bug the live run exposed: `4 * step` gave a 1d step a 4-day
        # lookback, so every point averaged four days of data.
        window_seconds = query.duration_seconds(query.auto_window(step))
        step_seconds = query.duration_seconds(step)
        assert window_seconds < 2 * step_seconds or step_seconds <= 60
