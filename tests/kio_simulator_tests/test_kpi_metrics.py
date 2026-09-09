"""Unit tests for kio_simulator.emit_real_kpi_metrics() — the D1.1 Table 8 KPI
emission gated by KIO_REAL_KPI_ROLE.

Every metric emitted here is unconditionally tagged source="simulated" (see the
function's own docstring for why: none of these has a real measurement path
yet), and every value is a percentage of the D1.1 baseline except KPI 8.2's MOS
(a 1-5 opinion score) and KPI 8.3 (a count).

These tests deliberately do NOT pin the exact simulated band of each KPI. Where
a band sits relative to its target is an arbitrary presentation choice on
generated data and gets re-tuned; asserting it here would only mean editing two
files instead of one. What matters, and what is asserted, is the contract: a
role emits exactly its own KPIs, once per call, correctly labelled, in a
plausible range for the KPI's unit — and never touches another role's
instruments.
"""
import pytest

# Broad sanity envelopes per unit, not the simulator's actual bands.
PCT_OF_BASELINE = (0.0, 200.0)   # % of baseline; 100 = baseline
MOS_SCALE = (1.0, 5.0)           # D1.1 KPI 8.2 Mean Opinion Score
RATIO = (0.0, 1.0)               # WP3 slicing success rate


class _FakeInstrument:
    """Stand-in for an OTel histogram/counter: records (value, labels) calls
    without touching any real exporter."""

    def __init__(self):
        self.calls = []

    def record(self, value, labels=None):
        self.calls.append((value, labels))

    def add(self, value, labels=None):
        self.calls.append((value, labels))


def _assert_emitted_once(fake, name, envelope, kio_id):
    assert len(fake.calls) == 1, f"{name} should be emitted exactly once per call"
    value, labels = fake.calls[0]
    lo, hi = envelope
    assert lo <= value <= hi, (name, value, envelope)
    assert labels["source"] == "simulated", name
    assert labels["kio.id"] == kio_id, name


def _patch_all(m, monkeypatch, names):
    fakes = {name: _FakeInstrument() for name in names}
    for name, fake in fakes.items():
        monkeypatch.setattr(m, name, fake)
    return fakes


def test_empty_role_emits_nothing(fresh_kio_module):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="")
    # Every KPI instrument is None for an empty role; if the function tried
    # to touch any of them it would raise AttributeError immediately.
    m.emit_real_kpi_metrics({"kio.id": "kioX"}, is_error=False)


def test_bugfix_role_emits_its_four_kpis(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="bugfix")
    fakes = _patch_all(m, monkeypatch, (
        "bugfix_time_hist",        # KPI 6.1
        "issue_resolution_hist",   # KPI 1.2
        "customer_reported_hist",  # KPI 6.2
        "slicing_success_hist",    # WP3 task metric, not a global KPI
        "fix_attempt_counter",
    ))
    monkeypatch.setattr(m, "_evaluate_fix_success", lambda: True)

    m.emit_real_kpi_metrics({"kio.id": "kio2-sim"}, is_error=False)

    for name in ("bugfix_time_hist", "issue_resolution_hist", "customer_reported_hist"):
        _assert_emitted_once(fakes[name], name, PCT_OF_BASELINE, "kio2-sim")
    _assert_emitted_once(fakes["slicing_success_hist"], "slicing_success_hist", RATIO, "kio2-sim")

    assert len(fakes["fix_attempt_counter"].calls) == 1
    _, outcome_labels = fakes["fix_attempt_counter"].calls[0]
    assert outcome_labels["outcome"] == "success"


def test_customer_reported_is_a_rate_emitted_every_call(fresh_kio_module, monkeypatch):
    """KPI 6.2 used to be a counter incremented on a rare error branch, which
    could not be compared to D1.1's target at all: D1.1 defines the KPI as
    "% of baseline issue rate" against a baseline of ~5 issues per released
    feature, so a raw count of issues had no denominator. It is now a rate,
    recorded on every call like every other percentage-of-baseline KPI —
    including when the request itself did not error.
    """
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="bugfix")
    fakes = _patch_all(m, monkeypatch, (
        "bugfix_time_hist", "issue_resolution_hist", "customer_reported_hist",
        "slicing_success_hist", "fix_attempt_counter",
    ))
    monkeypatch.setattr(m, "_evaluate_fix_success", lambda: False)

    m.emit_real_kpi_metrics({"kio.id": "kio2-sim"}, is_error=False)
    assert len(fakes["customer_reported_hist"].calls) == 1

    m.emit_real_kpi_metrics({"kio.id": "kio2-sim"}, is_error=True)
    assert len(fakes["customer_reported_hist"].calls) == 2


@pytest.mark.parametrize("role", ["nlp-requirements", "architecture-to-code"])
def test_codegen_roles_emit_shared_kpis(fresh_kio_module, monkeypatch, role):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE=role)
    names = ["codegen_speed_hist", "code_quality_hist"]  # KPI 1.1, KPI 3.1
    if role == "architecture-to-code":
        names.append("review_score_hist")               # KPI 3.2, KIO4 only
    fakes = _patch_all(m, monkeypatch, names)

    m.emit_real_kpi_metrics({"kio.id": "kioX"}, is_error=False)

    for name, fake in fakes.items():
        _assert_emitted_once(fake, name, PCT_OF_BASELINE, "kioX")

    if role == "nlp-requirements":
        # KIO3 never touches review_score_hist — it's None for this role, so
        # touching it would have raised AttributeError above.
        assert m.review_score_hist is None


def test_ai_sysdev_role_emits_all_five_kpis(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="ai-sysdev")
    fakes = _patch_all(m, monkeypatch, (
        "dev_productivity_hist",  # KPI 4.1
        "time_to_market_hist",    # KPI 5.1
        "annual_cost_hist",       # KPI 7.1
        "refactoring_hist",       # KPI 9.1
        "tech_debt_hist",         # KPI 9.2
    ))

    m.emit_real_kpi_metrics({"kio.id": "kio7"}, is_error=False)

    for name, fake in fakes.items():
        _assert_emitted_once(fake, name, PCT_OF_BASELINE, "kio7")


def test_ai_sysdev_role_does_not_touch_other_roles_kpis(fresh_kio_module):
    """Regression guard: kio7's role must not also fire kio2/kio3/kio4's
    instruments (all None here since only ai-sysdev's are created)."""
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="ai-sysdev")
    assert m.bugfix_time_hist is None
    assert m.codegen_speed_hist is None
    assert m.review_score_hist is None


def test_green_deploy_role_emits_energy_kpis(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="green-deploy")
    fakes = _patch_all(m, monkeypatch, (
        "lifecycle_energy_hist",    # KPI 2.1
        "deploy_energy_eff_hist",   # KPI 2.2
        "cross_arch_build_counter",  # KPI 8.3
    ))
    monkeypatch.setattr(m.random, "random", lambda: 0.99)  # skip the rare build-success branch

    m.emit_real_kpi_metrics({"kio.id": "kio8"}, is_error=False)

    for name in ("lifecycle_energy_hist", "deploy_energy_eff_hist"):
        _assert_emitted_once(fakes[name], name, PCT_OF_BASELINE, "kio8")

    # KPI 8.3 is a discrete "did a heterogeneous build succeed" event, not a
    # per-request measurement; only random() gates it, pinned above 0.05 here.
    assert fakes["cross_arch_build_counter"].calls == []


def test_green_deploy_role_cross_arch_build_fires_on_rare_branch(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="green-deploy")
    fakes = _patch_all(m, monkeypatch, (
        "lifecycle_energy_hist", "deploy_energy_eff_hist", "cross_arch_build_counter",
    ))
    monkeypatch.setattr(m.random, "random", lambda: 0.0)  # force the <0.05 branch to fire

    m.emit_real_kpi_metrics({"kio.id": "kio8"}, is_error=False)

    assert len(fakes["cross_arch_build_counter"].calls) == 1
    assert fakes["cross_arch_build_counter"].calls[0][0] == 1


def test_green_deploy_role_does_not_touch_other_roles_kpis(fresh_kio_module):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="green-deploy")
    assert m.bugfix_time_hist is None
    assert m.dev_productivity_hist is None
    assert m.adoption_rate_hist is None


def test_adoption_role_emits_both_kpis(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="adoption")
    fakes = _patch_all(m, monkeypatch, (
        "adoption_rate_hist",   # KPI 8.1, % of eligible users
        "adoption_usage_hist",  # KPI 8.2, % of developers
        "adoption_mos_hist",    # KPI 8.2, MOS 1-5
    ))

    m.emit_real_kpi_metrics({"kio.id": "kio13"}, is_error=False)

    # 8.1 and 8.2's usage half are percentages, but of eligible users rather
    # than of a baseline — D1.1 gives them a 0 % baseline, so the same envelope
    # is the right sanity check. The MOS half is on its own 1-5 scale.
    for name in ("adoption_rate_hist", "adoption_usage_hist"):
        _assert_emitted_once(fakes[name], name, PCT_OF_BASELINE, "kio13")
    _assert_emitted_once(fakes["adoption_mos_hist"], "adoption_mos_hist", MOS_SCALE, "kio13")


def test_adoption_role_does_not_touch_other_roles_kpis(fresh_kio_module):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="adoption")
    assert m.bugfix_time_hist is None
    assert m.lifecycle_energy_hist is None
    assert m.dev_productivity_hist is None
