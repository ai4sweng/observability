"""Unit tests for the metric registry lookup layer.

These assert the behaviour the API's 400-vs-200 contract rests on: a caller
who asks for something impossible gets told so, rather than getting an empty
success that looks like a broken telemetry pipeline.
"""
import pytest

from metrics_api import registry


class TestNameResolution:
    """Every spelling a caller might reasonably use resolves to one metric."""

    @pytest.mark.parametrize(
        "spelling",
        [
            "kio.request.duration_ms",   # OTel declaration name
            "kio_request_duration_ms",   # what VictoriaMetrics stores
            "request.duration_ms",       # prefix dropped
            "request_duration_ms",
        ],
    )
    def test_all_spellings_resolve_to_the_same_metric(self, spelling):
        assert registry.resolve(spelling).otel_name == "kio.request.duration_ms"

    def test_surrounding_whitespace_is_tolerated(self):
        assert registry.resolve("  request_count  ").otel_name == "kio.request.count"

    def test_unknown_metric_raises_with_suggestions(self):
        with pytest.raises(registry.UnknownMetric) as excinfo:
            registry.resolve("duration_hours")
        # The point of the suggestion list is that a 400 tells the caller what
        # they probably meant, not just that they were wrong.
        assert "kio.bugfix.duration_hours" in excinfo.value.suggestions

    def test_unknown_metric_with_no_near_match_still_raises_cleanly(self):
        with pytest.raises(registry.UnknownMetric):
            registry.resolve("not_a_metric_at_all_zzz")

    def test_resolve_many_preserves_order_and_dedupes(self):
        metrics = registry.resolve_many("request_count, kio.request.count, heartbeat")
        assert [m.otel_name for m in metrics] == ["kio.request.count", "kio.heartbeat"]

    def test_resolve_many_accepts_a_list(self):
        metrics = registry.resolve_many(["heartbeat"])
        assert [m.otel_name for m in metrics] == ["kio.heartbeat"]

    def test_resolve_many_propagates_unknown_names(self):
        with pytest.raises(registry.UnknownMetric):
            registry.resolve_many("request_count,bogus_metric")


class TestLabelValidation:
    """`source` is the label most likely to be filtered on, and it is absent
    from exactly five metrics — filtering those must be an error."""

    def test_source_is_valid_on_metrics_that_carry_it(self):
        registry.resolve("request_count").validate_labels({"source": "real"})

    @pytest.mark.parametrize(
        "metric_name",
        [
            "kio.heartbeat",
            "kio.session.active_count",
            "kio.repo.line_count",
            "kio.repo.file_count",
            "kio.repo.directory_count",
        ],
    )
    def test_source_filter_rejected_on_metrics_without_it(self, metric_name):
        metric = registry.resolve(metric_name)
        assert metric.supports_source is False
        with pytest.raises(registry.InvalidLabel) as excinfo:
            metric.validate_labels({"source": "real"})
        assert excinfo.value.label == "source"
        assert "kio_id" in excinfo.value.valid

    def test_base_labels_are_valid_everywhere(self):
        for metric in registry.ALL.values():
            metric.validate_labels({label: "x" for label in registry.BASE_LABELS})

    def test_unrelated_label_is_rejected(self):
        with pytest.raises(registry.InvalidLabel):
            registry.resolve("heartbeat").validate_labels({"direction": "input"})


class TestInstrumentClassification:
    """The query builder switches on these, so a wrong classification produces
    a silently meaningless number rather than an error."""

    @pytest.mark.parametrize(
        "metric_name,instrument",
        [
            ("kio.request.count", "counter"),
            ("kio.request.duration_ms", "histogram"),
            ("kio.session.active_count", "up_down_counter"),
            ("kio.repo.line_count", "gauge"),
            ("kio.llm.energy_joules", "counter"),
            ("kio.llm.tokens_per_second", "histogram"),
        ],
    )
    def test_instrument_types(self, metric_name, instrument):
        assert registry.resolve(metric_name).instrument == instrument

    def test_convenience_predicates_agree_with_instrument(self):
        assert registry.resolve("request_duration_ms").is_histogram
        assert registry.resolve("request_count").is_counter
        assert registry.resolve("session_active_count").is_gauge
        assert registry.resolve("repo_line_count").is_gauge


class TestRequiredGroupBy:
    """Dimensions that must be aggregated over by default, or the caller gets
    two series where they asked for one number."""

    @pytest.mark.parametrize(
        "metric_name,dimension",
        [
            ("kio.request.count", "status"),
            ("kio.llm.token_count", "direction"),
            ("kio.request.error_count", "error_type"),
            ("kio.fix.attempt_count", "outcome"),
        ],
    )
    def test_multi_dimensional_metrics_declare_their_dimension(self, metric_name, dimension):
        assert dimension in registry.resolve(metric_name).required_group_by

    def test_single_dimensional_metrics_declare_none(self):
        assert registry.resolve("request_duration_ms").required_group_by == ()


class TestFamilies:
    """The operational/KPI split mirrors the KIO_REAL_KPI_ROLE gate in the
    simulator, and is what keeps /api/metrics and /api/kpis separate."""

    def test_families_partition_the_registry(self):
        assert len(registry.operational()) + len(registry.kpis()) == len(registry.ALL)

    def test_operational_metrics_have_no_role(self):
        assert all(m.roles == () for m in registry.operational())

    def test_kpi_metrics_all_have_at_least_one_role(self):
        assert all(m.roles for m in registry.kpis())

    def test_mandatory_contract_metrics_are_operational(self):
        # Contract section 2.1's mandatory set must never land in the KPI family.
        mandatory = [
            "kio.request.count",
            "kio.request.duration_ms",
            "kio.request.error_count",
            "kio.llm.token_count",
            "kio.llm.cost_usd",
            "kio.session.active_count",
            "kio.heartbeat",
        ]
        for name in mandatory:
            assert registry.resolve(name).family == "operational"

    def test_dual_owned_kpi_reports_both_roles(self):
        # 1.1 and 3.1 are recorded by both kio3 and kio4's roles, which is why
        # a KPI response has to be an array.
        assert set(registry.resolve("kio.codegen.duration_minutes").roles) == {
            "nlp-requirements",
            "architecture-to-code",
        }

    def test_role_specific_kpi_reports_only_its_owner(self):
        assert registry.resolve("kio.review.score").roles == ("architecture-to-code",)


class TestOptionalPresence:
    """A missing series for a conditionally-emitted metric is expected, not a
    fault — the API needs to tell those apart."""

    def test_repo_gauges_require_the_code_analysis_task_type(self):
        metric = registry.resolve("kio.repo.line_count")
        assert metric.optional is True
        assert metric.requires_task_type == ("code-analysis",)

    def test_kpi_metrics_are_optional(self):
        assert all(m.optional for m in registry.kpis())

    def test_core_operational_metrics_are_not_optional(self):
        assert registry.resolve("kio.request.count").optional is False
        assert registry.resolve("kio.heartbeat").optional is False


class TestUnits:
    """Units come from the registry because VictoriaMetrics does not store
    them — if these go missing, every response loses its unit field."""

    def test_every_metric_has_a_unit(self):
        missing = [m.otel_name for m in registry.ALL.values() if not m.unit]
        assert missing == []

    @pytest.mark.parametrize(
        "metric_name,unit",
        [
            ("kio.request.duration_ms", "ms"),
            ("kio.llm.cost_usd", "USD"),
            ("kio.llm.tokens_per_second", "tokens/s"),
            ("kio.llm.energy_joules", "J"),
            ("kio.bugfix.duration_hours", "h"),
            ("kio.llm.gpu_temperature_celsius", "Cel"),
        ],
    )
    def test_declared_units(self, metric_name, unit):
        assert registry.resolve(metric_name).unit == unit
