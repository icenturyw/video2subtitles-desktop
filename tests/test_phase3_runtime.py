from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from engine.runtime.capabilities import (  # noqa: E402
    CommandCapability,
    RuntimeCapabilities,
    RuntimeSnapshot,
    probe_command,
)
from engine.runtime.gpu import GPUMetrics, NvidiaSmiMonitor, NullGPUMonitor, create_gpu_monitor  # noqa: E402
from engine.runtime.model_resources import (  # noqa: E402
    ModelDefinition,
    ModelResourceError,
    ModelResourceManager,
    ModelResourcePolicy,
    ModelState,
)
from engine.runtime.monitor import RuntimeMonitor  # noqa: E402
from engine.runtime.preflight import PreflightChecker  # noqa: E402


def _definition(
    loader=object,
    unloader=lambda _value: None,
    *,
    model_id="demo",
    policy=ModelResourcePolicy.IDLE,
    idle_timeout=1.0,
    device="cpu",
    vram=0,
    load_timeout=1.0,
):
    return ModelDefinition(
        model_id=model_id,
        kind="test",
        loader=loader,
        unloader=unloader,
        policy=policy,
        idle_timeout_seconds=idle_timeout,
        device=device,
        estimated_vram_mb=vram,
        load_timeout_seconds=load_timeout,
    )


def _snapshot(timestamp=1.0, cpu=10.0, memory=20.0, disk=1000, gpus=()):
    return RuntimeSnapshot(timestamp, cpu, 20, 100, memory, disk, 2000, tuple(gpus), ())


def test_null_gpu_monitor_is_safe_and_empty():
    monitor = NullGPUMonitor("cpu only")
    assert monitor.available is False
    assert monitor.sample() == []
    assert monitor.last_error == "cpu only"


def test_create_gpu_monitor_falls_back_without_nvidia():
    with patch("engine.runtime.gpu.shutil.which", return_value=None):
        assert isinstance(create_gpu_monitor(), NullGPUMonitor)


def test_nvidia_monitor_parses_metrics():
    completed = Mock(returncode=0, stdout="0, RTX 4090, 55, 1024, 24564, 47\n", stderr="")
    with patch("engine.runtime.gpu.shutil.which", return_value="nvidia-smi"), patch(
        "engine.runtime.gpu.subprocess.run", return_value=completed
    ) as run:
        metrics = NvidiaSmiMonitor(timeout_seconds=1.5).sample()
    assert metrics == [GPUMetrics(0, "RTX 4090", 55.0, 1024, 24564, 47.0)]
    assert metrics[0].memory_free_mb == 23540
    assert run.call_args.kwargs["timeout"] == 1.5


def test_nvidia_query_failure_does_not_raise():
    completed = Mock(returncode=1, stdout="", stderr="driver unavailable")
    with patch("engine.runtime.gpu.shutil.which", return_value="nvidia-smi"), patch(
        "engine.runtime.gpu.subprocess.run", return_value=completed
    ):
        monitor = NvidiaSmiMonitor()
        assert monitor.sample() == []
        assert "driver unavailable" in monitor.last_error


def test_nvidia_query_timeout_does_not_raise():
    import subprocess

    with patch("engine.runtime.gpu.shutil.which", return_value="nvidia-smi"), patch(
        "engine.runtime.gpu.subprocess.run",
        side_effect=subprocess.TimeoutExpired("nvidia-smi", 2),
    ):
        monitor = NvidiaSmiMonitor(timeout_seconds=2)
        assert monitor.sample() == []
        assert "timed out" in monitor.last_error


def test_probe_command_reports_missing_binary():
    with patch("engine.runtime.capabilities.shutil.which", return_value=None):
        result = probe_command("ffmpeg", ("-version",))
    assert result.available is False
    assert result.error_code == "FFMPEG_NOT_FOUND"


def test_probe_command_uses_timeout_and_version():
    completed = Mock(returncode=0, stdout="ffmpeg version 7\nmore", stderr="")
    with patch("engine.runtime.capabilities.shutil.which", return_value="ffmpeg"), patch(
        "engine.runtime.capabilities.subprocess.run", return_value=completed
    ) as run:
        result = probe_command("ffmpeg", ("-version",), 1.25)
    assert result.available is True
    assert result.version == "ffmpeg version 7"
    assert run.call_args.kwargs["timeout"] == 1.25


def test_runtime_capabilities_do_not_require_gpu(tmp_path):
    missing = CommandCapability(False, error_code="MISSING")
    with patch("engine.runtime.capabilities.probe_command", return_value=missing):
        caps = RuntimeCapabilities.detect(tmp_path, gpu_monitor=NullGPUMonitor())
    assert caps.cuda_available is False
    assert caps.gpus == ()
    assert caps.workspace == str(tmp_path.resolve())


def test_monitor_start_stop_and_rolling_window(tmp_path):
    counter = 0

    def sampler(*_args, **_kwargs):
        nonlocal counter
        counter += 1
        return _snapshot(timestamp=float(counter), cpu=float(counter))

    monitor = RuntimeMonitor(
        tmp_path,
        sampler=sampler,
        gpu_monitor=NullGPUMonitor(),
        active_interval=0.01,
        idle_interval=0.01,
        max_samples=3,
    )
    assert monitor.start() is True
    assert monitor.start() is False
    deadline = time.time() + 1
    while counter < 5 and time.time() < deadline:
        time.sleep(0.01)
    assert monitor.stop() is True
    assert monitor.running is False
    assert len(monitor.samples()) == 3


def test_monitor_uses_active_and_idle_intervals(tmp_path):
    active = False
    monitor = RuntimeMonitor(
        tmp_path,
        active_task_checker=lambda: active,
        gpu_monitor=NullGPUMonitor(),
        active_interval=2,
        idle_interval=11,
    )
    assert monitor.active_interval == 2
    assert monitor.idle_interval == 11


def test_monitor_summary_aggregates_without_persistence(tmp_path):
    values = iter([_snapshot(1, 10, 20, 1000), _snapshot(2, 30, 40, 900)])
    monitor = RuntimeMonitor(tmp_path, sampler=lambda *_a, **_k: next(values), gpu_monitor=NullGPUMonitor())
    monitor.sample_once()
    monitor.sample_once()
    summary = monitor.summary()
    assert summary["sample_count"] == 2
    assert summary["cpu_percent"]["average"] == 20
    assert summary["minimum_disk_free_bytes"] == 900


def test_same_model_is_loaded_once_and_reused():
    loader = Mock(return_value={"model": 1})
    manager = ModelResourceManager()
    definition = _definition(loader=loader)
    first = manager.acquire(definition)
    second = manager.acquire(definition)
    assert loader.call_count == 1
    assert first.value is second.value
    assert manager.status()[0]["ref_count"] == 2
    first.release()
    second.release()


def test_equivalent_fresh_adapter_definitions_reuse_resident_model():
    loader = Mock(return_value={"model": 1})
    manager = ModelResourceManager()
    first = manager.acquire(_definition(loader=loader))
    second = manager.acquire(_definition(loader=loader))
    assert loader.call_count == 1
    assert manager.status()[0]["ref_count"] == 2
    first.release()
    second.release()


def test_model_lease_duplicate_release_is_idempotent():
    manager = ModelResourceManager()
    lease = manager.acquire(_definition())
    assert lease.release() is True
    assert lease.release() is False
    assert manager.status()[0]["ref_count"] == 0


def test_immediate_policy_unloads_on_last_release():
    unloader = Mock()
    manager = ModelResourceManager()
    lease = manager.acquire(_definition(unloader=unloader, policy=ModelResourcePolicy.IMMEDIATE))
    lease.release()
    assert unloader.call_count == 1
    assert manager.status()[0]["state"] == ModelState.UNLOADED.value


def test_idle_policy_unloads_after_timeout_only():
    unloader = Mock()
    manager = ModelResourceManager()
    lease = manager.acquire(_definition(unloader=unloader, idle_timeout=5))
    lease.release()
    last_used = manager.status()[0]["last_used_at"]
    assert manager.evict_idle(now=last_used + 4) == []
    assert manager.evict_idle(now=last_used + 5) == ["demo"]


def test_keep_loaded_policy_survives_idle_eviction():
    manager = ModelResourceManager()
    lease = manager.acquire(_definition(policy=ModelResourcePolicy.KEEP_LOADED))
    lease.release()
    assert manager.evict_idle(now=time.monotonic() + 10000) == []
    assert manager.status()[0]["state"] == "loaded"


def test_active_model_cannot_be_unloaded():
    unloader = Mock()
    manager = ModelResourceManager()
    lease = manager.acquire(_definition(unloader=unloader))
    assert manager.unload("demo") is False
    assert unloader.call_count == 0
    lease.release()


def test_memory_pressure_unloads_idle_lru_model():
    free = {"mb": 100}

    def unload(_value):
        free["mb"] = 1000

    manager = ModelResourceManager(available_vram_mb=lambda: free["mb"])
    old = manager.acquire(_definition(model_id="old", unloader=unload))
    old.release()
    evicted = manager.relieve_memory_pressure(500)
    assert evicted == ["old"]


def test_memory_pressure_never_unloads_leased_model():
    manager = ModelResourceManager(available_vram_mb=lambda: 0)
    lease = manager.acquire(_definition(model_id="busy"))
    assert manager.relieve_memory_pressure(100) == []
    assert manager.status()[0]["state"] == "loaded"
    lease.release()


def test_exclusive_sidecar_group_never_unloads_active_model():
    unloaded = Mock()
    manager = ModelResourceManager()
    first_definition = ModelDefinition(
        model_id="qwen:first", kind="tts", loader=object, unloader=unloaded,
        resource_group="qwen-sidecar",
    )
    second_definition = ModelDefinition(
        model_id="qwen:second", kind="tts", loader=object, unloader=unloaded,
        resource_group="qwen-sidecar",
    )
    lease = manager.acquire(first_definition)
    with pytest.raises(ModelResourceError, match="in use"):
        manager.acquire(second_definition)
    assert unloaded.call_count == 0
    lease.release()


def test_insufficient_vram_returns_stable_error_code():
    manager = ModelResourceManager(available_vram_mb=lambda: 100)
    definition = _definition(device="cuda:0", vram=500)
    with pytest.raises(ModelResourceError) as caught:
        manager.acquire(definition)
    assert caught.value.error_code == "MODEL_RESOURCE_UNAVAILABLE"
    assert manager.status()[0]["state"] == "failed"


def test_loader_failure_sets_failed_state():
    manager = ModelResourceManager()
    definition = _definition(loader=Mock(side_effect=RuntimeError("bad weights")))
    with pytest.raises(ModelResourceError, match="bad weights"):
        manager.acquire(definition)
    assert manager.status()[0]["state"] == "failed"


def test_loader_timeout_sets_failed_state():
    blocker = threading.Event()
    manager = ModelResourceManager()
    definition = _definition(loader=lambda: blocker.wait(1), load_timeout=0.02)
    with pytest.raises(ModelResourceError, match="timed out"):
        manager.acquire(definition)
    assert manager.status()[0]["state"] == "failed"
    blocker.set()


def test_late_loader_result_is_cleaned_after_timeout():
    blocker = threading.Event()
    unloaded = threading.Event()
    manager = ModelResourceManager()

    def load():
        blocker.wait(1)
        return "late-model"

    definition = _definition(
        loader=load,
        unloader=lambda value: unloaded.set() if value == "late-model" else None,
        load_timeout=0.02,
    )
    with pytest.raises(ModelResourceError, match="timed out"):
        manager.acquire(definition)
    blocker.set()
    assert unloaded.wait(1)


def test_concurrent_acquire_uses_one_loader_without_deadlock():
    loader = Mock(side_effect=lambda: (time.sleep(0.03), object())[1])
    manager = ModelResourceManager()
    definition = _definition(loader=loader, load_timeout=1)
    leases = []

    def acquire():
        leases.append(manager.acquire(definition))

    threads = [threading.Thread(target=acquire) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(1)
        assert not thread.is_alive()
    assert loader.call_count == 1
    assert len(leases) == 4
    for lease in leases:
        lease.release()


def test_shutdown_refuses_to_unload_active_lease():
    manager = ModelResourceManager()
    lease = manager.acquire(_definition())
    assert manager.shutdown() == ["demo"]
    lease.release()
    assert manager.shutdown() == []


def _preflight_caps(tmp_path, *, ffmpeg=True, disk_free=10**10, gpus=()):
    command = CommandCapability(ffmpeg, path="tool" if ffmpeg else "", error_code="" if ffmpeg else "FFMPEG_NOT_FOUND")
    return RuntimeCapabilities(
        "Windows", "test", "x86_64", 8, 16 * 1024**3, str(tmp_path), 100 * 1024**3,
        disk_free, command, command, bool(gpus), "test", tuple(gpus), "",
    )


def test_preflight_requires_input_and_does_not_crash(tmp_path):
    checker = PreflightChecker(gpu_monitor=NullGPUMonitor(), minimum_disk_free_bytes=0)
    with patch("engine.runtime.preflight.RuntimeCapabilities.detect", return_value=_preflight_caps(tmp_path)):
        result = checker.check({"workspace_dir": str(tmp_path)})
    assert any(issue.code == "SOURCE_INPUT_REQUIRED" for issue in result.errors)
    assert result.can_start is False


def test_preflight_reports_missing_ffmpeg(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("x", encoding="utf-8")
    checker = PreflightChecker(gpu_monitor=NullGPUMonitor(), minimum_disk_free_bytes=0)
    with patch("engine.runtime.preflight.RuntimeCapabilities.detect", return_value=_preflight_caps(tmp_path, ffmpeg=False)):
        result = checker.check({"workspace_dir": str(tmp_path), "source_subtitle": str(source)})
    assert {issue.code for issue in result.errors} >= {"FFMPEG_NOT_FOUND"}


def test_preflight_reports_disk_shortage(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("subtitle", encoding="utf-8")
    checker = PreflightChecker(gpu_monitor=NullGPUMonitor(), minimum_disk_free_bytes=1000)
    with patch("engine.runtime.preflight.RuntimeCapabilities.detect", return_value=_preflight_caps(tmp_path, disk_free=10)):
        result = checker.check({"workspace_dir": str(tmp_path), "source_subtitle": str(source)})
    assert any(issue.code == "DISK_SPACE_INSUFFICIENT" for issue in result.errors)


def test_preflight_rejects_cuda_without_gpu(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("subtitle", encoding="utf-8")
    checker = PreflightChecker(gpu_monitor=NullGPUMonitor(), minimum_disk_free_bytes=0)
    with patch("engine.runtime.preflight.RuntimeCapabilities.detect", return_value=_preflight_caps(tmp_path)):
        result = checker.check({
            "workspace_dir": str(tmp_path), "source_subtitle": str(source),
            "tts_options": {"device": "cuda"},
        })
    assert any(issue.code == "MODEL_DEVICE_INCOMPATIBLE" for issue in result.errors)


def test_preflight_unknown_tts_provider_is_structured(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("subtitle", encoding="utf-8")
    checker = PreflightChecker(
        gpu_monitor=NullGPUMonitor(), minimum_disk_free_bytes=0,
        tts_provider_exists=lambda name: name == "known",
    )
    with patch("engine.runtime.preflight.RuntimeCapabilities.detect", return_value=_preflight_caps(tmp_path)):
        result = checker.check({
            "workspace_dir": str(tmp_path), "source_subtitle": str(source),
            "dubbing_enabled": True, "tts_provider": "missing",
        })
    issue = next(issue for issue in result.errors if issue.code == "TTS_PROVIDER_NOT_FOUND")
    assert issue.field == "tts_provider"


def test_preflight_warning_allows_confirmation(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("subtitle", encoding="utf-8")
    outside = tmp_path.parent / "outside-phase3"
    checker = PreflightChecker(gpu_monitor=NullGPUMonitor(), minimum_disk_free_bytes=0)
    with patch("engine.runtime.preflight.RuntimeCapabilities.detect", return_value=_preflight_caps(tmp_path)):
        result = checker.check({
            "workspace_dir": str(tmp_path), "source_subtitle": str(source),
            "output_dir": str(outside),
        })
    assert result.can_start is True
    assert result.requires_confirmation is True
    assert result.to_dict()["warnings"][0]["severity"] == "warning"
