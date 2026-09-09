"""Drift detection: the committed registry must match the simulator's source.

metrics_api/registry_data.py is generated from kio-simulator/kio_simulator.py
but committed, because the metrics API's Docker image does not have the
simulator in its build context. That means nothing forces the two to stay in
sync at runtime — this test is what does.

If this fails, the fix is to regenerate, not to edit registry_data.py:

    python scripts/generate_metric_registry.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from metrics_api.registry_data import METRICS as COMMITTED

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_metric_registry.py"
SIMULATOR = REPO_ROOT / "kio-simulator" / "kio_simulator.py"

REGENERATE_HINT = "run: python scripts/generate_metric_registry.py"


def _load_generator():
    """Import the generator by path — scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("_registry_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def freshly_extracted():
    generator = _load_generator()
    return generator.finalize(
        generator.extract(SIMULATOR.read_text(encoding="utf-8"))
    )


def test_no_metrics_added_or_removed(freshly_extracted):
    added = sorted(set(freshly_extracted) - set(COMMITTED))
    removed = sorted(set(COMMITTED) - set(freshly_extracted))
    assert (added, removed) == ([], []), (
        f"registry is stale — added in source: {added}, "
        f"no longer in source: {removed}. {REGENERATE_HINT}"
    )


def test_every_field_matches_source(freshly_extracted):
    differences = {
        name: {
            field: {"committed": COMMITTED[name][field], "source": value}
            for field, value in entry.items()
            if COMMITTED[name].get(field) != value
        }
        for name, entry in freshly_extracted.items()
        if name in COMMITTED
    }
    differences = {name: diff for name, diff in differences.items() if diff}
    assert differences == {}, f"registry is stale: {differences}. {REGENERATE_HINT}"


def test_extraction_is_deterministic(freshly_extracted):
    """Two runs must agree, or the drift test itself would be flaky and the
    generated file would churn in diffs."""
    generator = _load_generator()
    again = generator.finalize(generator.extract(SIMULATOR.read_text(encoding="utf-8")))
    assert again == freshly_extracted


def test_extraction_found_a_plausible_number_of_metrics(freshly_extracted):
    """Guards against the extractor silently matching nothing — a parser bug
    that returned {} would otherwise make every comparison above pass once
    the empty result was committed."""
    assert len(freshly_extracted) >= 30
    families = {entry["family"] for entry in freshly_extracted.values()}
    assert families == {"operational", "kpi"}
