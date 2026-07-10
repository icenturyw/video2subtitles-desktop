"""Reference-counted lifecycle management for local AI models."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict


class ModelState(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    UNLOADING = "unloading"
    FAILED = "failed"


class ModelResourcePolicy(str, Enum):
    IMMEDIATE = "immediate"
    IDLE = "idle"
    KEEP_LOADED = "keep_loaded"


class ModelResourceError(RuntimeError):
    def __init__(self, message: str, error_code: str = "MODEL_RESOURCE_UNAVAILABLE") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ModelDefinition:
    model_id: str
    kind: str
    loader: Callable[[], Any]
    unloader: Callable[[Any], None]
    device: str = "cpu"
    estimated_vram_mb: int = 0
    policy: ModelResourcePolicy = ModelResourcePolicy.IDLE
    idle_timeout_seconds: float = 120.0
    load_timeout_seconds: float = 300.0
    metadata: dict = field(default_factory=dict)


@dataclass
class _ModelEntry:
    definition: ModelDefinition
    state: ModelState = ModelState.UNLOADED
    value: Any = None
    ref_count: int = 0
    last_used_at: float = 0.0
    error: str = ""
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))


class ModelLease:
    def __init__(self, manager: "ModelResourceManager", model_id: str, value: Any) -> None:
        self._manager = manager
        self.model_id = model_id
        self.value = value
        self._released = False
        self._lock = threading.Lock()

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> bool:
        with self._lock:
            if self._released:
                return False
            self._released = True
        self._manager.release(self.model_id)
        return True

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


class ModelResourceManager:
    def __init__(
        self,
        *,
        available_vram_mb: Callable[[], int] | None = None,
        event_sink: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._entries: Dict[str, _ModelEntry] = {}
        self._lock = threading.RLock()
        self._available_vram_mb = available_vram_mb or (lambda: 0)
        self._event_sink = event_sink or (lambda _event, _payload: None)

    def register(self, definition: ModelDefinition, *, replace: bool = False) -> None:
        model_id = definition.model_id.strip()
        if not model_id:
            raise ValueError("model_id is required")
        with self._lock:
            existing = self._entries.get(model_id)
            if existing and not replace:
                if existing.definition != definition:
                    raise ValueError(f"Model already registered: {model_id}")
                return
            if existing and existing.ref_count:
                raise ModelResourceError("Cannot replace a model with active leases")
            self._entries[model_id] = _ModelEntry(definition=definition)

    def acquire(self, definition: ModelDefinition, timeout: float | None = None) -> ModelLease:
        self.register(definition)
        entry = self._entries[definition.model_id]
        deadline = time.monotonic() + (timeout if timeout is not None else definition.load_timeout_seconds)
        while True:
            with entry.condition:
                if entry.state == ModelState.LOADED:
                    entry.ref_count += 1
                    entry.last_used_at = time.monotonic()
                    return ModelLease(self, definition.model_id, entry.value)
                if entry.state in {ModelState.LOADING, ModelState.UNLOADING}:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ModelResourceError(f"Timed out waiting for model {definition.model_id}")
                    entry.condition.wait(remaining)
                    continue
                entry.state = ModelState.LOADING
                entry.error = ""
                break

        self._ensure_capacity(definition)
        self._emit("model_loading", definition.model_id, definition)
        try:
            value = _call_with_timeout(definition.loader, max(0.01, deadline - time.monotonic()))
        except Exception as exc:
            with entry.condition:
                entry.state = ModelState.FAILED
                entry.error = str(exc)
                entry.condition.notify_all()
            self._emit("model_failed", definition.model_id, definition, error=str(exc))
            if isinstance(exc, ModelResourceError):
                raise
            raise ModelResourceError(f"Failed to load {definition.model_id}: {exc}") from exc
        with entry.condition:
            entry.value = value
            entry.state = ModelState.LOADED
            entry.ref_count = 1
            entry.last_used_at = time.monotonic()
            entry.condition.notify_all()
        self._emit("model_loaded", definition.model_id, definition)
        return ModelLease(self, definition.model_id, value)

    def release(self, model_id: str) -> bool:
        entry = self._entry(model_id)
        should_unload = False
        with entry.condition:
            if entry.ref_count <= 0:
                return False
            entry.ref_count -= 1
            entry.last_used_at = time.monotonic()
            should_unload = entry.ref_count == 0 and entry.definition.policy == ModelResourcePolicy.IMMEDIATE
        if should_unload:
            self.unload(model_id)
        return True

    def unload(self, model_id: str, *, force: bool = False) -> bool:
        entry = self._entry(model_id)
        with entry.condition:
            if entry.ref_count and not force:
                return False
            if entry.state in {ModelState.UNLOADED, ModelState.FAILED}:
                entry.state = ModelState.UNLOADED
                entry.value = None
                return False
            if entry.state != ModelState.LOADED:
                return False
            entry.state = ModelState.UNLOADING
            value = entry.value
        self._emit("model_unloading", model_id, entry.definition)
        try:
            entry.definition.unloader(value)
        except Exception as exc:
            with entry.condition:
                entry.state = ModelState.FAILED
                entry.error = str(exc)
                entry.condition.notify_all()
            return False
        with entry.condition:
            entry.value = None
            entry.state = ModelState.UNLOADED
            entry.error = ""
            entry.condition.notify_all()
        self._emit("model_unloaded", model_id, entry.definition)
        return True

    def evict_idle(self, *, now: float | None = None) -> list[str]:
        current = time.monotonic() if now is None else now
        evicted: list[str] = []
        for model_id, entry in self._entry_items():
            with entry.condition:
                eligible = (
                    entry.state == ModelState.LOADED
                    and entry.ref_count == 0
                    and entry.definition.policy == ModelResourcePolicy.IDLE
                    and current - entry.last_used_at >= entry.definition.idle_timeout_seconds
                )
            if eligible and self.unload(model_id):
                evicted.append(model_id)
        return evicted

    def relieve_memory_pressure(self, required_free_mb: int = 0) -> list[str]:
        candidates = sorted(
            self._entry_items(),
            key=lambda pair: pair[1].last_used_at,
        )
        evicted: list[str] = []
        for model_id, entry in candidates:
            if self._available_vram_mb() >= required_free_mb:
                break
            with entry.condition:
                eligible = entry.state == ModelState.LOADED and entry.ref_count == 0
            if eligible and self.unload(model_id):
                evicted.append(model_id)
        return evicted

    def loaded_models(self) -> list[dict]:
        return [item for item in self.status() if item["state"] == ModelState.LOADED.value]

    def status(self) -> list[dict]:
        result: list[dict] = []
        for model_id, entry in self._entry_items():
            with entry.condition:
                result.append({
                    "model_id": model_id,
                    "kind": entry.definition.kind,
                    "device": entry.definition.device,
                    "state": entry.state.value,
                    "ref_count": entry.ref_count,
                    "policy": entry.definition.policy.value,
                    "estimated_vram_mb": entry.definition.estimated_vram_mb,
                    "last_used_at": entry.last_used_at,
                    "error": entry.error,
                })
        return result

    def shutdown(self) -> list[str]:
        failed: list[str] = []
        for model_id, entry in self._entry_items():
            with entry.condition:
                if entry.ref_count:
                    failed.append(model_id)
                    continue
            if entry.state == ModelState.LOADED and not self.unload(model_id):
                failed.append(model_id)
        return failed

    def _ensure_capacity(self, definition: ModelDefinition) -> None:
        needed = max(0, int(definition.estimated_vram_mb))
        if not needed or definition.device.lower().startswith("cpu"):
            return
        available = int(self._available_vram_mb())
        if available < needed:
            self.relieve_memory_pressure(needed)
            available = int(self._available_vram_mb())
        if available < needed:
            entry = self._entries[definition.model_id]
            with entry.condition:
                entry.state = ModelState.FAILED
                entry.error = f"Requires {needed} MB VRAM; {available} MB available"
                entry.condition.notify_all()
            raise ModelResourceError(entry.error)

    def _entry(self, model_id: str) -> _ModelEntry:
        with self._lock:
            try:
                return self._entries[model_id]
            except KeyError as exc:
                raise KeyError(f"Unknown model: {model_id}") from exc

    def _entry_items(self) -> list[tuple[str, _ModelEntry]]:
        with self._lock:
            return list(self._entries.items())

    def _emit(self, event: str, model_id: str, definition: ModelDefinition, **extra: Any) -> None:
        payload = {"model_id": model_id, "kind": definition.kind, "device": definition.device, **extra}
        try:
            self._event_sink(event, payload)
        except Exception:
            pass


def _call_with_timeout(callback: Callable[[], Any], timeout: float) -> Any:
    result: list[Any] = []
    error: list[BaseException] = []
    completed = threading.Event()

    def run() -> None:
        try:
            result.append(callback())
        except BaseException as exc:  # propagated in the caller thread
            error.append(exc)
        finally:
            completed.set()

    thread = threading.Thread(target=run, name="model-loader", daemon=True)
    thread.start()
    if not completed.wait(max(0.01, timeout)):
        raise ModelResourceError("Model loading timed out")
    if error:
        raise error[0]
    return result[0] if result else None
