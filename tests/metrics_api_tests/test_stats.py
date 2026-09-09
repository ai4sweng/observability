"""Unit tests for the summary statistics."""
import pytest

from metrics_api.stats import percentile, summarize


class TestSummarize:
    def test_basic_statistics(self):
        summary = summarize([10.0, 20.0, 30.0, 40.0])
        assert summary.min == 10.0
        assert summary.max == 40.0
        assert summary.avg == 25.0
        assert summary.samples == 4

    def test_current_defaults_to_the_last_sample(self):
        assert summarize([1.0, 2.0, 99.0]).current == 99.0

    def test_current_can_be_supplied_explicitly(self):
        # For when the caller already fetched a separate instant value.
        assert summarize([1.0, 2.0], current=5.0).current == 5.0

    def test_empty_input_yields_nulls_not_zeros(self):
        # Zero is a real measurement; "no samples" is not, and reporting the
        # second as the first is exactly the confusion to avoid.
        summary = summarize([])
        assert summary.samples == 0
        assert (summary.avg, summary.min, summary.max, summary.p95) == (None, None, None, None)

    def test_single_sample(self):
        summary = summarize([7.5])
        assert (summary.min, summary.max, summary.avg, summary.p95) == (7.5, 7.5, 7.5, 7.5)

    def test_as_dict_is_json_ready(self):
        assert set(summarize([1.0]).as_dict()) == {
            "current", "avg", "min", "max", "p95", "samples",
        }


class TestPercentile:
    def test_interpolates_linearly(self):
        # numpy's default 'linear' method, so an ad-hoc cross-check agrees.
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5

    def test_p95_of_a_hundred_points(self):
        assert percentile([float(i) for i in range(1, 101)], 0.95) == pytest.approx(95.05)

    def test_p100_is_the_maximum(self):
        assert percentile([1.0, 5.0, 3.0], 1.0) == 5.0

    def test_p0_is_the_minimum(self):
        assert percentile([4.0, 1.0, 3.0], 0.0) == 1.0

    def test_unsorted_input_is_handled(self):
        assert percentile([3.0, 1.0, 2.0], 0.5) == 2.0

    def test_empty_is_none(self):
        assert percentile([], 0.95) is None
