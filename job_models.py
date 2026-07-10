"""Unified job, subtitle, and artifact data models for Video2Subtitles."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pipeline_types import PipelineStage, ProcessingMode, SubtitleMode


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

class ErrorCode:
    """Standardized error codes for pipeline failures."""

    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    INVALID_JOB_SPEC = "INVALID_JOB_SPEC"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_SUBTITLE_NOT_FOUND = "SOURCE_SUBTITLE_NOT_FOUND"
    WORKSPACE_NOT_WRITABLE = "WORKSPACE_NOT_WRITABLE"
    FFMPEG_NOT_FOUND = "FFMPEG_NOT_FOUND"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    TRANSLATION_AUTH_FAILED = "TRANSLATION_AUTH_FAILED"
    TRANSLATION_RATE_LIMITED = "TRANSLATION_RATE_LIMITED"
    TRANSLATION_TIMEOUT = "TRANSLATION_TIMEOUT"
    TRANSLATION_INVALID_RESPONSE = "TRANSLATION_INVALID_RESPONSE"
    TRANSLATION_INCOMPLETE = "TRANSLATION_INCOMPLETE"
    SUBTITLE_INVALID_TIMELINE = "SUBTITLE_INVALID_TIMELINE"
    SUBTITLE_RENDER_FAILED = "SUBTITLE_RENDER_FAILED"
    WHISPERX_NOT_INSTALLED = "WHISPERX_NOT_INSTALLED"
    WHISPERX_FAILED = "WHISPERX_FAILED"
    TTS_NOT_INSTALLED = "TTS_NOT_INSTALLED"
    TTS_AUTH_FAILED = "TTS_AUTH_FAILED"
    TTS_FAILED = "TTS_FAILED"
    TTS_EMPTY_INPUT = "TTS_EMPTY_INPUT"
    TTS_GENERATION_FAILED = "TTS_GENERATION_FAILED"
    TTS_NO_AUDIO_OUTPUT = "TTS_NO_AUDIO_OUTPUT"
    TTS_ZERO_BYTE_AUDIO = "TTS_ZERO_BYTE_AUDIO"
    TTS_VOICE_PROFILE_MISMATCH = "TTS_VOICE_PROFILE_MISMATCH"
    TTS_PROMPT_AUDIO_INVALID = "TTS_PROMPT_AUDIO_INVALID"
    TTS_TIMBRE_INCONSISTENT = "TTS_TIMBRE_INCONSISTENT"
    TTS_CHUNK_GENERATION_FAILED = "TTS_CHUNK_GENERATION_FAILED"
    TTS_AUDIO_NORMALIZE_FAILED = "TTS_AUDIO_NORMALIZE_FAILED"
    AUDIO_MIX_FAILED = "AUDIO_MIX_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_INTERRUPTED = "TASK_INTERRUPTED"
    DISK_SPACE_LOW = "DISK_SPACE_LOW"


_ERROR_MESSAGES = {
    ErrorCode.ENGINE_UNAVAILABLE: "本地化引擎不可用，请检查 8766 端口服务是否启动",
    ErrorCode.INVALID_JOB_SPEC: "任务参数无效",
    ErrorCode.SOURCE_NOT_FOUND: "找不到源视频文件",
    ErrorCode.SOURCE_SUBTITLE_NOT_FOUND: "找不到源字幕文件",
    ErrorCode.WORKSPACE_NOT_WRITABLE: "工作目录不可写",
    ErrorCode.FFMPEG_NOT_FOUND: "未找到 FFmpeg，请安装并确保在 PATH 中",
    ErrorCode.FFMPEG_FAILED: "FFmpeg 处理失败",
    ErrorCode.TRANSLATION_AUTH_FAILED: "翻译 API 认证失败，请检查 API Key",
    ErrorCode.TRANSLATION_RATE_LIMITED: "翻译请求过于频繁，请稍后再试",
    ErrorCode.TRANSLATION_TIMEOUT: "翻译请求超时",
    ErrorCode.TRANSLATION_INVALID_RESPONSE: "翻译返回数据格式异常",
    ErrorCode.TRANSLATION_INCOMPLETE: "翻译结果不完整",
    ErrorCode.SUBTITLE_INVALID_TIMELINE: "字幕时间轴无效",
    ErrorCode.SUBTITLE_RENDER_FAILED: "字幕渲染失败",
    ErrorCode.WHISPERX_NOT_INSTALLED: "WhisperX 未安装",
    ErrorCode.WHISPERX_FAILED: "WhisperX 处理失败",
    ErrorCode.TTS_NOT_INSTALLED: "TTS 服务未安装",
    ErrorCode.TTS_AUTH_FAILED: "TTS 认证失败",
    ErrorCode.TTS_FAILED: "语音合成失败",
    ErrorCode.TTS_EMPTY_INPUT: "没有可用于 TTS 的字幕文本，转写或翻译结果为空",
    ErrorCode.TTS_GENERATION_FAILED: "TTS 语音合成执行异常",
    ErrorCode.TTS_NO_AUDIO_OUTPUT: "TTS 执行结束，但没有发现任何有效音频文件",
    ErrorCode.TTS_ZERO_BYTE_AUDIO: "TTS 生成了音频文件，但文件大小均为 0",
    ErrorCode.TTS_VOICE_PROFILE_MISMATCH: "TTS 音色配置不一致，检测到同一任务使用了不同的音色配置",
    ErrorCode.TTS_PROMPT_AUDIO_INVALID: "TTS 参考音频无效或格式不支持",
    ErrorCode.TTS_TIMBRE_INCONSISTENT: "TTS 音色一致性检测失败",
    ErrorCode.TTS_CHUNK_GENERATION_FAILED: "TTS 字幕合并生成失败",
    ErrorCode.TTS_AUDIO_NORMALIZE_FAILED: "TTS 音频标准化处理失败",
    ErrorCode.AUDIO_MIX_FAILED: "音频混合失败",
    ErrorCode.TASK_CANCELLED: "任务已取消",
    ErrorCode.TASK_INTERRUPTED: "任务被中断（服务重启或崩溃）",
    ErrorCode.DISK_SPACE_LOW: "磁盘空间不足",
}


def error_message(code: str) -> str:
    """Return user-friendly Chinese message for an error code."""
    return _ERROR_MESSAGES.get(code, code)


# ---------------------------------------------------------------------------
# SubtitleStyle
# ---------------------------------------------------------------------------

@dataclass
class SubtitleStyle:
    """Subtitle appearance style configuration."""

    preset: str = "default"
    font_family: str = "Microsoft YaHei"
    font_size: int = 28
    primary_color: str = "&H00FFFFFF"
    secondary_color: str = "&H0000FFFF"
    outline_color: str = "&H00000000"
    background_color: str = "&H80000000"
    outline: float = 2.0
    shadow: float = 1.0
    margin_v: int = 50
    alignment: int = 2
    bold: bool = False
    bilingual_source_scale: float = 0.8
    bilingual_translation_scale: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubtitleStyle":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def presets(cls) -> Dict[str, "SubtitleStyle"]:
        return {
            "default": cls(),
            "netflix": cls(
                preset="netflix", font_size=32, outline=3.0, shadow=0.0,
                primary_color="&H00FFFFFF", outline_color="&H00000000",
            ),
            "youtube": cls(
                preset="youtube", font_size=26, outline=2.0, shadow=2.0,
                background_color="&HA0000000",
            ),
            "bilingual": cls(
                preset="bilingual", font_size=26,
                bilingual_source_scale=0.75, bilingual_translation_scale=1.0,
            ),
            "mobile_vertical": cls(
                preset="mobile_vertical", font_size=24, margin_v=60,
            ),
        }


# ---------------------------------------------------------------------------
# WordTiming (for WhisperX word-level timestamps)
# ---------------------------------------------------------------------------

@dataclass
class WordTiming:
    """Word-level timing from WhisperX or similar engines."""

    word: str
    start: float
    end: float
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WordTiming":
        return cls(
            word=str(data.get("word", "")),
            start=float(data.get("start", 0)),
            end=float(data.get("end", 0)),
            score=float(data.get("score", 0)),
        )


# ---------------------------------------------------------------------------
# SubtitleSegment
# ---------------------------------------------------------------------------

@dataclass
class SubtitleSegment:
    """A single subtitle segment with timing, text, and optional translation."""

    index: int
    start: float
    end: float
    text: str
    translation: str = ""
    speaker: Optional[str] = None
    words: List[WordTiming] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty if valid)."""
        errors = []
        if self.start < 0:
            errors.append(f"segment {self.index}: start ({self.start}) < 0")
        if self.end <= self.start:
            errors.append(f"segment {self.index}: end ({self.end}) <= start ({self.start})")
        if not self.text.strip():
            errors.append(f"segment {self.index}: text is empty")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Remove empty optional fields for cleaner serialization
        if not d.get("translation"):
            d.pop("translation", None)
        if not d.get("speaker"):
            d.pop("speaker", None)
        if not d.get("words"):
            d.pop("words", None)
        if not d.get("metadata"):
            d.pop("metadata", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubtitleSegment":
        words_data = data.get("words", [])
        words = [WordTiming.from_dict(w) for w in words_data]
        return cls(
            index=int(data.get("index", 0)),
            start=float(data.get("start", 0)),
            end=float(data.get("end", 0)),
            text=str(data.get("text", "")).strip(),
            translation=str(data.get("translation", "") or "").strip(),
            speaker=data.get("speaker"),
            words=words,
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_srt_dict(cls, data: Dict[str, Any], index: int = 0) -> "SubtitleSegment":
        """Create from the simpler dict format used by subtitle_utils.parse_srt_text."""
        return cls(
            index=index,
            start=float(data.get("start", 0)),
            end=float(data.get("end", 0)),
            text=str(data.get("text", "")).strip(),
        )

    def to_srt_dict(self) -> Dict[str, Any]:
        """Convert to the simpler dict format used by subtitle_utils."""
        result: Dict[str, Any] = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        if self.translation:
            result["translation"] = self.translation
        return result


def segments_from_srt_dicts(srt_dicts: List[Dict[str, Any]]) -> List[SubtitleSegment]:
    """Convert a list of parse_srt_text results to SubtitleSegments."""
    return [SubtitleSegment.from_srt_dict(d, i + 1) for i, d in enumerate(srt_dicts)]


def validate_segments(segments: List[SubtitleSegment]) -> List[str]:
    """Validate a list of segments and return all errors."""
    errors = []
    for seg in segments:
        errors.extend(seg.validate())
    # Check timeline ordering
    for i in range(1, len(segments)):
        if segments[i].start < segments[i - 1].start:
            errors.append(
                f"timeline disorder: segment {segments[i].index} start "
                f"({segments[i].start}) < previous start ({segments[i - 1].start})"
            )
    return errors


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

ArtifactKind = Literal[
    "source_video",
    "source_audio",
    "source_srt",
    "translated_srt",
    "bilingual_srt",
    "source_ass",
    "translated_ass",
    "bilingual_ass",
    "softsub_video",
    "burned_video",
    "tts_audio",
    "dubbed_video",
    "log",
]


@dataclass
class Artifact:
    """A pipeline output artifact."""

    kind: str
    path: str
    language: Optional[str] = None
    created_at: str = ""
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("language") is None:
            d.pop("language", None)
        if not d.get("created_at"):
            d.pop("created_at", None)
        if not d.get("size_bytes"):
            d.pop("size_bytes", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        return cls(
            kind=str(data.get("kind", "")),
            path=str(data.get("path", "")),
            language=data.get("language"),
            created_at=str(data.get("created_at", "")),
            size_bytes=int(data.get("size_bytes", 0)),
        )

    @classmethod
    def from_path(cls, kind: str, file_path: Path, base_dir: Path,
                  language: Optional[str] = None) -> "Artifact":
        """Create artifact from a file path, computing relative path and size."""
        try:
            rel = str(Path(file_path).resolve().relative_to(Path(base_dir).resolve()))
        except Exception:
            rel = str(file_path)
        size = 0
        try:
            size = Path(file_path).stat().st_size
        except Exception:
            pass
        return cls(
            kind=kind,
            path=rel,
            language=language,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            size_bytes=size,
        )


# ---------------------------------------------------------------------------
# TranslationConfig
# ---------------------------------------------------------------------------

@dataclass
class TranslationConfig:
    """Configuration for the translation provider."""

    provider: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "V2S_TRANSLATION_API_KEY"
    api_type: Literal["auto", "responses", "chat_completions", "anthropic_messages"] = "auto"
    temperature: float = 0.3
    timeout: int = 60
    max_batch_chars: int = 16000
    max_batch_items: int = 50
    retry_count: int = 3
    concurrency: int = 8
    quality_mode: Literal["fast", "quality"] = "fast"
    output_format: Literal["json", "compact"] = "compact"
    # auto: strict JSON batch first, then split/retry bad items;
    # strict_json_batch: force id-mapped JSON output;
    # single_segment: safest mode, one request per subtitle segment.
    batch_mode: Literal["auto", "strict_json_batch", "single_segment"] = "auto"
    stream: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranslationConfig":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# JobSpec
# ---------------------------------------------------------------------------

@dataclass
class JobSpec:
    """Specification for a localization pipeline job."""

    job_id: str = ""
    source: str = ""
    source_type: Literal["local", "url"] = "local"
    mode: str = ProcessingMode.SUBTITLE.value
    source_language: str = "auto"
    target_language: Optional[str] = None
    subtitle_mode: str = SubtitleMode.BILINGUAL.value
    burn_subtitles: bool = False
    embed_soft_subtitles: bool = False
    dubbing_enabled: bool = False
    translation_provider: Optional[TranslationConfig] = None
    tts_provider: Optional[str] = None
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    workspace_dir: str = ""

    def __post_init__(self):
        if not self.job_id:
            self.job_id = str(uuid.uuid4())

    def validate(self) -> List[str]:
        """Return list of validation errors."""
        errors = []
        if not self.source:
            errors.append("source is required")
        if not self.workspace_dir:
            errors.append("workspace_dir is required")
        if self.mode in (ProcessingMode.TRANSLATE.value, ProcessingMode.DUB.value):
            if not self.target_language:
                errors.append("target_language is required for translate/dub mode")
            if not self.translation_provider:
                errors.append("translation_provider is required for translate/dub mode")
        if self.mode == ProcessingMode.DUB.value and not self.tts_provider:
            errors.append("tts_provider is required for dub mode")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("translation_provider") is not None:
            d["translation_provider"] = self.translation_provider.to_dict() if self.translation_provider else None
        d["subtitle_style"] = self.subtitle_style.to_dict()
        if d.get("target_language") is None:
            d.pop("target_language", None)
        if d.get("tts_provider") is None:
            d.pop("tts_provider", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobSpec":
        translation_data = data.get("translation_provider")
        translation = TranslationConfig.from_dict(translation_data) if translation_data else None
        style_data = data.get("subtitle_style", {})
        style = SubtitleStyle.from_dict(style_data) if style_data else SubtitleStyle()
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items()
                    if k in known and k not in ("translation_provider", "subtitle_style")}
        return cls(
            translation_provider=translation,
            subtitle_style=style,
            **filtered,
        )


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    """Result of a pipeline job, suitable for API responses and manifest."""

    job_id: str = ""
    status: str = "pending"
    stage: str = PipelineStage.PREPARE.value
    progress: int = 0
    message: str = ""
    detected_language: str = ""
    segments: List[SubtitleSegment] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    error_code: Optional[str] = None
    error_detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "detected_language": self.detected_language,
            "segments": [s.to_dict() for s in self.segments],
            "artifacts": [a.to_dict() for a in self.artifacts],
        }
        if self.error_code:
            d["error_code"] = self.error_code
        if self.error_detail:
            d["error_detail"] = self.error_detail
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskResult":
        segments = [SubtitleSegment.from_dict(s) for s in data.get("segments", [])]
        artifacts = [Artifact.from_dict(a) for a in data.get("artifacts", [])]
        return cls(
            job_id=str(data.get("job_id", "")),
            status=str(data.get("status", "pending")),
            stage=str(data.get("stage", PipelineStage.PREPARE.value)),
            progress=int(data.get("progress", 0)),
            message=str(data.get("message", "")),
            detected_language=str(data.get("detected_language", "")),
            segments=segments,
            artifacts=artifacts,
            error_code=data.get("error_code"),
            error_detail=data.get("error_detail"),
        )

    def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts.append(artifact)

    def find_artifacts(self, kind: str) -> List[Artifact]:
        return [a for a in self.artifacts if a.kind == kind]
