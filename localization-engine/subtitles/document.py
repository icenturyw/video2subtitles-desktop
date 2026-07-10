"""Editable subtitle documents with stable cue IDs and integer milliseconds."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class SubtitleCue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str = ""
    translated_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.cue_id).strip():
            raise ValueError("cue_id is required")
        if isinstance(self.start_ms, bool) or not isinstance(self.start_ms, int):
            raise TypeError("start_ms must be an integer")
        if isinstance(self.end_ms, bool) or not isinstance(self.end_ms, int):
            raise TypeError("end_ms must be an integer")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleCue":
        return cls(
            cue_id=str(data.get("cue_id") or ""),
            start_ms=int(data.get("start_ms", 0)),
            end_ms=int(data.get("end_ms", 0)),
            source_text=str(data.get("source_text") or ""),
            translated_text=str(data.get("translated_text") or ""),
            metadata=copy.deepcopy(data.get("metadata") or {}),
        )

    def updated(self, **changes: Any) -> "SubtitleCue":
        if "metadata" in changes:
            changes["metadata"] = copy.deepcopy(changes["metadata"])
        return replace(self, **changes)


@dataclass
class SubtitleDocument:
    document_id: str
    task_id: str
    version: int
    cues: list[SubtitleCue]
    source_language: str = ""
    target_language: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "document_id": self.document_id,
            "task_id": self.task_id,
            "version": self.version,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": copy.deepcopy(self.metadata),
            "cues": [cue.to_dict() for cue in self.cues],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleDocument":
        return cls(
            document_id=str(data.get("document_id") or ""),
            task_id=str(data.get("task_id") or ""),
            version=int(data.get("version", 0)),
            cues=[SubtitleCue.from_dict(item) for item in data.get("cues") or []],
            source_language=str(data.get("source_language") or ""),
            target_language=str(data.get("target_language") or ""),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
            metadata=copy.deepcopy(data.get("metadata") or {}),
        )

    @classmethod
    def from_segments(
        cls,
        task_id: str,
        segments: Iterable[Any],
        *,
        source_language: str = "",
        target_language: str = "",
    ) -> "SubtitleDocument":
        document_id = uuid.uuid5(uuid.NAMESPACE_URL, f"video2subtitles:{task_id}").hex
        occurrences: dict[str, int] = {}
        cues: list[SubtitleCue] = []
        for segment in segments:
            start_ms = round(float(getattr(segment, "start", 0.0)) * 1000)
            end_ms = round(float(getattr(segment, "end", 0.0)) * 1000)
            source = str(getattr(segment, "text", "") or "")
            translated = str(getattr(segment, "translation", "") or "")
            fingerprint = hashlib.sha256(
                f"{start_ms}|{end_ms}|{source}".encode("utf-8")
            ).hexdigest()
            occurrence = occurrences.get(fingerprint, 0)
            occurrences[fingerprint] = occurrence + 1
            cue_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"video2subtitles:{task_id}:{fingerprint}:{occurrence}",
            ).hex
            cues.append(SubtitleCue(cue_id, start_ms, end_ms, source, translated))
        return cls(
            document_id=document_id,
            task_id=task_id,
            version=0,
            cues=cues,
            source_language=source_language,
            target_language=target_language,
        )

    def clone(self, *, cues: list[SubtitleCue] | None = None, version: int | None = None) -> "SubtitleDocument":
        return SubtitleDocument(
            document_id=self.document_id,
            task_id=self.task_id,
            version=self.version if version is None else version,
            cues=list(self.cues if cues is None else cues),
            source_language=self.source_language,
            target_language=self.target_language,
            created_at=self.created_at,
            updated_at=utc_now(),
            metadata=copy.deepcopy(self.metadata),
        )

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def checksum(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def new_cue_id() -> str:
    return uuid.uuid4().hex
