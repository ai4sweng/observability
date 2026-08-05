"""Unit tests for kio_simulator.emit_real_kpi_metrics() — the D1.1 KPI
emission gated by KIO_REAL_KPI_ROLE. Every metric emitted here is
unconditionally tagged source="simulated" (see the function's own docstring
for why: none of these have a real measurement path yet)."""
import pytest


class _FakeInstrument:
    """Stand-in for an OTel histogram/counter: records (value, labels) calls
    without touching any real exporter."""

    def __init__(self):
        self.calls = []

    def record(self, value, labels=None):
        self.calls.append((value, labels))

    def add(self, value, labels=None):
        self.calls.append((value, labels))


def test_empty_role_emits_nothing(fresh_kio_module):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="")
    # Every KPI instrument is None for an empty role; if the function tried
    # to touch any of them it would raise AttributeError immediately.
    m.emit_real_kpi_metrics({"kio.id": "kioX"}, is_error=False)


def test_bugfix_role_emits_bugfix_kpis(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="bugfix")
    fakes = {
        "bugfix_duration_hist": _FakeInstrument(),
        "issue_resolution_hist": _FakeInstrument(),
        "slicing_success_hist": _FakeInstrument(),
        "customer_reported_counter": _FakeInstrument(),
        "fix_attempt_counter": _FakeInstrument(),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(m, name, fake)
    monkeypatch.setattr(m, "_evaluate_fix_success", lambda: True)
    monkeypatch.setattr(m.random, "random", lambda: 0.99)  # skip the rare customer-reported branch

    m.emit_real_kpi_metrics({"kio.id": "kio2-sim"}, is_error=False)

    assert len(fakes["bugfix_duration_hist"].calls) == 1
    value, labels = fakes["bugfix_duration_hist"].calls[0]
    assert 6.0 <= value <= 10.0
    assert labels["source"] == "simulated"
    assert labels["kio.id"] == "kio2-sim"

    assert len(fakes["issue_resolution_hist"].calls) == 1
    assert 5.0 <= fakes["issue_resolution_hist"].calls[0][0] <= 9.0

    assert len(fakes["slicing_success_hist"].calls) == 1
    assert 0.75 <= fakes["slicing_success_hist"].calls[0][0] <= 0.97

    # is_error=False makes the customer-reported branch unreachable no matter
    # what random() returns.
    assert fakes["customer_reported_counter"].calls == []

    assert len(fakes["fix_attempt_counter"].calls) == 1
    _, outcome_labels = fakes["fix_attempt_counter"].calls[0]
    assert outcome_labels["outcome"] == "success"


def test_bugfix_role_customer_reported_fires_on_rare_error_branch(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="bugfix")
    for name in ("bugfix_duration_hist", "issue_resolution_hist", "slicing_success_hist", "fix_attempt_counter"):
        monkeypatch.setattr(m, name, _FakeInstrument())
    fake_customer = _FakeInstrument()
    monkeypatch.setattr(m, "customer_reported_counter", fake_customer)
    monkeypatch.setattr(m, "_evaluate_fix_success", lambda: False)
    monkeypatch.setattr(m.random, "random", lambda: 0.0)  # force the <0.05 branch to fire

    m.emit_real_kpi_metrics({"kio.id": "kio2-sim"}, is_error=True)

    assert len(fake_customer.calls) == 1
    count, _ = fake_customer.calls[0]
    assert count in (1, 2)


@pytest.mark.parametrize("role", ["nlp-requirements", "architecture-to-code"])
def test_codegen_roles_emit_shared_kpis(fresh_kio_module, monkeypatch, role):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE=role)
    fake_duration = _FakeInstrument()
    fake_quality = _FakeInstrument()
    monkeypatch.setattr(m, "codegen_duration_hist", fake_duration)
    monkeypatch.setattr(m, "code_quality_hist", fake_quality)
    if role == "architecture-to-code":
        fake_review = _FakeInstrument()
        monkeypatch.setattr(m, "review_score_hist", fake_review)

    m.emit_real_kpi_metrics({"kio.id": "kioX"}, is_error=False)

    assert len(fake_duration.calls) == 1
    assert 65.0 <= fake_duration.calls[0][0] <= 95.0
    assert len(fake_quality.calls) == 1
    assert 65.0 <= fake_quality.calls[0][0] <= 90.0

    if role == "architecture-to-code":
        assert len(fake_review.calls) == 1
        assert 3.6 <= fake_review.calls[0][0] <= 4.4
    else:
        # KIO3 (nlp-requirements) never touches review_score_hist — it's None
        # for this role; touching it would have raised AttributeError.
        assert m.review_score_hist is None


def test_ai_sysdev_role_emits_all_five_kpis(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="ai-sysdev")
    names = (
        "dev_productivity_hist", "time_to_market_hist", "cost_saving_hist",
        "refactoring_hist", "tech_debt_hist",
    )
    fakes = {name: _FakeInstrument() for name in names}
    for name, fake in fakes.items():
        monkeypatch.setattr(m, name, fake)

    m.emit_real_kpi_metrics({"kio.id": "kio7"}, is_error=False)

    ranges = {
        "dev_productivity_hist": (0.6, 1.1),
        "time_to_market_hist": (24.0, 38.0),
        "cost_saving_hist": (12.0, 28.0),
        "refactoring_hist": (2.5, 4.5),
        "tech_debt_hist": (1.8, 3.5),
    }
    for name, fake in fakes.items():
        assert len(fake.calls) == 1, name
        value, labels = fake.calls[0]
        lo, hi = ranges[name]
        assert lo <= value <= hi, (name, value)
        assert labels["source"] == "simulated"


def test_ai_sysdev_role_does_not_touch_other_roles_kpis(fresh_kio_module):
    """Regression guard: kio7's role must not also fire kio2/kio3/kio4's
    instruments (all None here since only ai-sysdev's are created)."""
    m = fresh_kio_module(KIO_REAL_KPI_ROLE="ai-sysdev")
    assert m.bugfix_duration_hist is None
    assert m.codegen_duration_hist is None
    assert m.review_score_hist is None
