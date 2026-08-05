"""Unit tests for kio_simulator._read_gpu_temperature_celsius() — the
best-effort, two-tier (power-exporter HTTP endpoint, then local NVML) real
GPU temperature reader. Every path must degrade to None on failure and never
raise (mirrors _read_gpu_power_watts' sibling implementation, see its
docstring)."""
import sys

import requests


def test_returns_none_when_no_source_configured(fresh_kio_module):
    # No GPU_POWER_EXPORTER_URL, and this test environment has no real GPU
    # for NVML to find -> nvmlInit() itself raises -> caught -> None.
    m = fresh_kio_module(GPU_POWER_EXPORTER_URL="")
    assert m._read_gpu_temperature_celsius() is None


def test_reads_from_power_exporter_when_temperature_present(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(GPU_POWER_EXPORTER_URL="http://fake-exporter:9999/power")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"watts": 120.0, "temperature_c": 61.5}

    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _FakeResponse())

    assert m._read_gpu_temperature_celsius() == 61.5


def test_falls_back_to_nvml_when_exporter_has_no_temperature_field(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(GPU_POWER_EXPORTER_URL="http://fake-exporter:9999/power")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"watts": 120.0}  # no temperature_c key at all -> fall through to NVML

    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _FakeResponse())
    monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml(temp=47))

    assert m._read_gpu_temperature_celsius() == 47.0


def test_exporter_unreachable_falls_back_to_nvml(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(GPU_POWER_EXPORTER_URL="http://fake-exporter:9999/power")

    def _raise(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", _raise)
    monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml(temp=55))

    assert m._read_gpu_temperature_celsius() == 55.0


def test_nvml_failure_also_degrades_to_none(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(GPU_POWER_EXPORTER_URL="")

    def _broken_init():
        raise RuntimeError("NVML library not found")

    monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml(temp=99, init=_broken_init))

    assert m._read_gpu_temperature_celsius() is None


def _fake_pynvml(temp, init=None):
    import types
    return types.SimpleNamespace(
        nvmlInit=init or (lambda: None),
        nvmlDeviceGetHandleByIndex=lambda i: "fake-handle",
        nvmlDeviceGetTemperature=lambda handle, sensor: temp,
        NVML_TEMPERATURE_GPU=0,
    )
