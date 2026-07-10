"""Structured checks performed before a pipeline task starts."""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from engine.workspace import WorkspaceError, WorkspaceManager

from .capabilities import RuntimeCapabilities
from .gpu import GPUMonitor, create_gpu_monitor


@dataclass(frozen=True)
class PreflightIssue:
    severity: str
    code: str
    message: str
    field: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreflightResult:
    errors: list[PreflightIssue] = field(default_factory=list)
    warnings: list[PreflightIssue] = field(default_factory=list)
    capabilities: dict = field(default_factory=dict)

    @property
    def can_start(self) -> bool:
        return not self.errors

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.warnings)

    def to_dict(self) -> dict:
        return {
            "can_start": self.can_start,
            "requires_confirmation": self.requires_confirmation,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "capabilities": self.capabilities,
        }


class PreflightChecker:
    def __init__(
        self,
        *,
        gpu_monitor: GPUMonitor | None = None,
        tts_provider_exists: Callable[[str], bool] | None = None,
        command_timeout: float = 3.0,
        minimum_disk_free_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self.gpu_monitor = gpu_monitor or create_gpu_monitor(command_timeout)
        self.tts_provider_exists = tts_provider_exists or (lambda _name: True)
        self.command_timeout = command_timeout
        self.minimum_disk_free_bytes = max(0, int(minimum_disk_free_bytes))

    def check(self, request: dict[str, Any]) -> PreflightResult:
        result = PreflightResult()
        workspace_text = str(request.get("workspace_dir") or "").strip()
        if not workspace_text:
            self._error(result, "WORKSPACE_REQUIRED", "A workspace directory is required", "workspace_dir")
            return result
        try:
            workspace = WorkspaceManager(workspace_text).ensure()
        except (WorkspaceError, OSError, ValueError) as exc:
            self._error(result, "WORKSPACE_INVALID", str(exc), "workspace_dir", "Choose a writable task workspace")
            return result

        capabilities = RuntimeCapabilities.detect(
            workspace.root,
            gpu_monitor=self.gpu_monitor,
            command_timeout=self.command_timeout,
        )
        result.capabilities = capabilities.to_dict()
        self._check_input(request, result)
        self._check_workspace_write(workspace.root, result)
        self._check_disk(request, capabilities.disk_free_bytes, result)
        if not capabilities.ffmpeg.available:
            self._error(result, capabilities.ffmpeg.error_code or "FFMPEG_NOT_FOUND", "FFmpeg is unavailable", "ffmpeg", "Install FFmpeg and add it to PATH")
        if not capabilities.ffprobe.available:
            self._error(result, capabilities.ffprobe.error_code or "FFPROBE_NOT_FOUND", "FFprobe is unavailable", "ffprobe", "Install FFmpeg and add it to PATH")
        self._check_translation(request, result)
        self._check_tts(request, result)
        self._check_device(request, capabilities, result)
        self._check_output(request, workspace.root, result)
        return result

    def _check_input(self, request: dict, result: PreflightResult) -> None:
        candidates = [request.get("source_subtitle"), request.get("source_video")]
        provided = [Path(str(value)).expanduser() for value in candidates if str(value or "").strip()]
        if not provided:
            self._error(result, "SOURCE_INPUT_REQUIRED", "A source video or subtitle is required", "source_video")
            return
        for path in provided:
            if not path.is_file():
                self._error(result, "SOURCE_INPUT_NOT_FOUND", f"Input file does not exist: {path}", "source_video")
            elif not os.access(path, os.R_OK):
                self._error(result, "SOURCE_INPUT_UNREADABLE", f"Input file is not readable: {path}", "source_video")

    def _check_workspace_write(self, root: Path, result: PreflightResult) -> None:
        try:
            with tempfile.NamedTemporaryFile(prefix=".preflight-", dir=root, delete=True):
                pass
        except OSError as exc:
            self._error(result, "WORKSPACE_NOT_WRITABLE", f"Workspace is not writable: {exc}", "workspace_dir")

    def _check_disk(self, request: dict, free_bytes: int, result: PreflightResult) -> None:
        input_sizes = []
        for key in ("source_video", "source_subtitle"):
            path = Path(str(request.get(key) or ""))
            if path.is_file():
                input_sizes.append(path.stat().st_size)
        estimate = max(self.minimum_disk_free_bytes, sum(input_sizes) * 3)
        if free_bytes < estimate:
            self._error(result, "DISK_SPACE_INSUFFICIENT", f"Need approximately {estimate} free bytes; {free_bytes} available", "workspace_dir", "Free disk space or choose another workspace")
        elif free_bytes < estimate * 2:
            self._warning(result, "DISK_SPACE_LOW", "Workspace disk space is close to the estimated requirement", "workspace_dir")

    def _check_translation(self, request: dict, result: PreflightResult) -> None:
        source = str(request.get("source_language") or "auto").lower()
        target = str(request.get("target_language") or "").lower()
        if not target or source == target:
            return
        config = request.get("translation") or {}
        provider = str(config.get("provider") or "openai_compatible")
        if provider in {"openai_compatible", "openai-compatible"}:
            key_env = str(config.get("api_key_env") or "V2S_TRANSLATION_API_KEY")
            if not os.environ.get(key_env) and not config.get("api_key"):
                self._error(result, "TRANSLATION_PROVIDER_NOT_CONFIGURED", f"Translation credential {key_env} is missing", "translation", "Configure the translation provider before starting")
            if not str(config.get("base_url") or "").strip():
                self._warning(result, "TRANSLATION_BASE_URL_DEFAULTED", "Translation base URL is empty; provider default will be used", "translation")

    def _check_tts(self, request: dict, result: PreflightResult) -> None:
        if not bool(request.get("dubbing_enabled")):
            return
        provider = str(request.get("tts_provider") or "").strip()
        if not provider:
            self._error(result, "TTS_PROVIDER_REQUIRED", "A TTS provider is required for dubbing", "tts_provider")
            return
        try:
            exists = bool(self.tts_provider_exists(provider))
        except Exception:
            exists = False
        if not exists:
            self._error(result, "TTS_PROVIDER_NOT_FOUND", f"Unknown TTS provider: {provider}", "tts_provider")

    def _check_device(self, request: dict, capabilities: RuntimeCapabilities, result: PreflightResult) -> None:
        options = request.get("tts_options") or {}
        device = str(options.get("device") or request.get("device") or "auto").lower()
        if device.startswith("cuda") and not capabilities.gpus:
            self._error(result, "MODEL_DEVICE_INCOMPATIBLE", "CUDA was requested but no usable NVIDIA GPU was detected", "device", "Choose CPU/auto or fix the GPU runtime")
            return
        required = int(options.get("min_vram_mb") or 0)
        if required and capabilities.gpus:
            available = max(gpu.memory_free_mb for gpu in capabilities.gpus)
            if available < required:
                self._error(result, "MODEL_RESOURCE_UNAVAILABLE", f"Model needs {required} MB VRAM; {available} MB is free", "device", "Use a smaller model, CPU mode, or close other GPU workloads")

    def _check_output(self, request: dict, workspace: Path, result: PreflightResult) -> None:
        raw = str(request.get("output_dir") or "").strip()
        if not raw:
            return
        output = Path(raw).expanduser().resolve()
        if output == Path(output.anchor):
            self._error(result, "OUTPUT_DIRECTORY_UNSAFE", "A filesystem root cannot be used as task output", "output_dir")
        try:
            output.relative_to(workspace)
        except ValueError:
            self._warning(result, "OUTPUT_OUTSIDE_WORKSPACE", "Output is outside the task workspace", "output_dir", "Confirm this destination before starting")

    @staticmethod
    def _error(result: PreflightResult, code: str, message: str, field: str = "", suggestion: str = "") -> None:
        result.errors.append(PreflightIssue("error", code, message, field, suggestion))

    @staticmethod
    def _warning(result: PreflightResult, code: str, message: str, field: str = "", suggestion: str = "") -> None:
        result.warnings.append(PreflightIssue("warning", code, message, field, suggestion))
