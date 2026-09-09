"""Unit tests for the VictoriaMetrics client: response parsing and, mostly,
error classification.

The classification is the point. "unreachable", "query rejected" and "ran fine,
matched nothing" have to stay three separate outcomes — collapsing them is how
a broken telemetry pipeline gets reported as a value of zero.
"""
import pytest
import requests

from metrics_api.victoriametrics import (
    UpstreamQueryError,
    UpstreamUnavailable,
    VictoriaMetricsClient,
)


class _FakeResponse:
    def __init__(self, payload, status_code=200, json_raises=False):
        self._payload = payload
        self.status_code = status_code
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def vm(monkeypatch):
    """A client whose session returns whatever the test queues up."""
    client = VictoriaMetricsClient(base_url="http://vm:8428", timeout=1.0)
    state = {}

    def fake_get(url, params=None, timeout=None):
        state["url"] = url
        state["params"] = params
        if isinstance(state["response"], Exception):
            raise state["response"]
        return state["response"]

    monkeypatch.setattr(client._session, "get", fake_get)
    client._state = state
    return client


def _vector(value):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {"__name__": "kio_request_count", "kio_id": "kio2-sim"},
                        "value": [1_757_000_000, value]}],
        },
    }


def _matrix(values):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {"kio_id": "kio2-sim"},
                        "values": [[1_757_000_000 + i * 60, v] for i, v in enumerate(values)]}],
        },
    }


class TestInstantQuery:
    def test_parses_a_vector_result(self, vm):
        vm._state["response"] = _FakeResponse(_vector("4.25"))
        series = vm.query("sum(rate(kio_request_count[5m]))")
        assert len(series) == 1
        assert series[0].latest == 4.25

    def test_strips_the_internal_name_label(self, vm):
        vm._state["response"] = _FakeResponse(_vector("1"))
        # __name__ is Prometheus plumbing, not a label a caller filters on.
        assert vm.query("x")[0].labels == {"kio_id": "kio2-sim"}

    def test_empty_result_is_an_empty_list_not_an_error(self, vm):
        vm._state["response"] = _FakeResponse({"status": "success", "data": {"result": []}})
        assert vm.query("x") == []

    def test_hits_the_prometheus_query_path(self, vm):
        vm._state["response"] = _FakeResponse(_vector("1"))
        vm.query("expr")
        assert vm._state["url"] == "http://vm:8428/api/v1/query"
        assert vm._state["params"]["query"] == "expr"


class TestRangeQuery:
    def test_parses_a_matrix_result(self, vm):
        vm._state["response"] = _FakeResponse(_matrix(["1", "2", "3"]))
        series = vm.query_range("expr", start="-1h", end="now", step="60s")
        assert series[0].values == [1.0, 2.0, 3.0]

    def test_passes_the_window_through_untouched(self, vm):
        vm._state["response"] = _FakeResponse(_matrix(["1"]))
        vm.query_range("expr", start="-24h", end="now", step="864s")
        params = vm._state["params"]
        assert (params["start"], params["end"], params["step"]) == ("-24h", "now", "864s")

    def test_nan_becomes_none_rather_than_a_float_nan(self, vm):
        # A float NaN is not JSON-serialisable and would break the response.
        vm._state["response"] = _FakeResponse(_matrix(["1", "NaN", "3"]))
        series = vm.query_range("expr", start="-1h", end="now", step="60s")
        assert [s.value for s in series[0].samples] == [1.0, None, 3.0]
        assert series[0].values == [1.0, 3.0]

    def test_infinity_is_also_dropped(self, vm):
        vm._state["response"] = _FakeResponse(_matrix(["1", "+Inf"]))
        series = vm.query_range("expr", start="-1h", end="now", step="60s")
        assert series[0].values == [1.0]

    def test_latest_skips_trailing_nans(self, vm):
        vm._state["response"] = _FakeResponse(_matrix(["1", "2", "NaN"]))
        assert vm.query_range("expr", start="-1h", end="now", step="60s")[0].latest == 2.0


class TestErrorClassification:
    @pytest.mark.parametrize(
        "exception",
        [
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.Timeout("timed out"),
        ],
    )
    def test_network_failure_is_upstream_unavailable(self, vm, exception):
        vm._state["response"] = exception
        with pytest.raises(UpstreamUnavailable) as excinfo:
            vm.query("x")
        assert "http://vm:8428" in str(excinfo.value)

    def test_server_error_is_upstream_unavailable(self, vm):
        vm._state["response"] = _FakeResponse({}, status_code=503)
        with pytest.raises(UpstreamUnavailable):
            vm.query("x")

    def test_non_json_body_is_upstream_unavailable(self, vm):
        vm._state["response"] = _FakeResponse(None, json_raises=True)
        with pytest.raises(UpstreamUnavailable):
            vm.query("x")

    def test_query_rejection_is_a_query_error_not_unavailable(self, vm):
        # A bad query is the API's fault (or the caller's), not the store being
        # down — it must not be reported as a 502.
        vm._state["response"] = _FakeResponse(
            {"status": "error", "error": "unexpected token \"}\""}, status_code=422
        )
        with pytest.raises(UpstreamQueryError) as excinfo:
            vm.query("bad{")
        assert "unexpected token" in str(excinfo.value)

    def test_client_error_without_a_message_still_classifies(self, vm):
        vm._state["response"] = _FakeResponse({}, status_code=400)
        with pytest.raises(UpstreamQueryError):
            vm.query("x")


class TestDefaults:
    def test_base_url_trailing_slash_is_normalised(self):
        assert VictoriaMetricsClient("http://vm:8428/").base_url == "http://vm:8428"
