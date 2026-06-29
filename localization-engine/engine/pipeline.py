"""Pipeline orchestrator for the localization engine.

Full pipeline stages:
    prepare -> normalize -> translate -> subtitle_export -> render -> finalize

Each stage checks cancellation and progress, updates the task store,
and supports retry from any stage via checkpoints.
"""
from __future__ import annotations

import logging
import os
import json
import shutil
import threading
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
_ENGINE_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _ENGINE_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.cancellation import CancellationToken
from engine.progress import ProgressTracker
from engine.task_store import TaskStore
from engine.workspace import (
    ensure_log_dir,
    get_source_subtitle,
    get_source_video,
    resolve_workspace,
    write_log,
)

try:
    from tts.voice_profile import TtsVoiceProfile, voice_profile_hash, profile_to_log_dict
    from tts.chunking import build_tts_chunks, TtsChunk
    from tts.planner import build_tts_plan
    from audio.normalize import normalize_tts_audio
    _HAS_VOICE_PROFILE = True
except ImportError:
    _HAS_VOICE_PROFILE = False
    TtsVoiceProfile = None
    voice_profile_hash = None
    profile_to_log_dict = None
    build_tts_chunks = None
    build_tts_plan = None
    TtsChunk = None
    normalize_tts_audio = None

logger = logging.getLogger("engine.pipeline")
_PIPELINE_LOG = "localization.log"
_QWEN3_DEFAULT_STABLE_SEED = 42


# Import from project-level modules and engine sibling packages
_ENGINE_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_ENGINE_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR_FOR_IMPORT))

try:
    from job_models import (
        Artifact, PipelineStage, ProcessingMode, SubtitleMode, SubtitleSegment,
        SubtitleStyle, TaskResult, TranslationConfig, segments_from_srt_dicts,
    )
    from subtitle_ass import segments_to_ass, save_ass
    from services.ffmpeg_service import find_ffmpeg, render_hardsub, render_softsub
    from subtitles.normalize import read_subtitle_file
    from subtitles.srt_writer import write_srt, write_vtt, write_txt
    from subtitles.validate import validate_timeline, validate_translation
    from subtitle_utils import (
        find_repeated_subtitle_runs,
        is_speech_subtitle_text,
        normalize_subtitle_text,
        normalize_subtitle_timeline,
        reconstruct_split_words,
    )
    from translation.batching import (
        CheckpointManager, batch_segments, batch_to_request,
    )
    from translation.glossary import Glossary as Gloss
    from translation.openai_compatible import get_provider
    from translation.quality import (
        has_blocking_issues, punctuation_only, validate_translation_items,
    )
except ImportError as e:
    logger.warning("Pipeline import error (some features may be unavailable): %s", e)


_STAGE_ORDER = [
    "prepare", "normalize", "translate", "subtitle_export",
    "tts", "audio_mix", "render", "finalize",
]


class PipelineRunner:
    def __init__(self, task_store: TaskStore, progress: ProgressTracker):
        self._store = task_store
        self._progress = progress

    def run_job(self, job_id: str, request: Dict[str, Any],
                cancel_token: CancellationToken) -> None:
        try:
            self._execute(job_id, request, cancel_token)
        except Exception as exc:
            logger.exception("Pipeline failed for job %s", job_id)
            self._store.update(
                job_id, status="error", stage="error",
                message=f"Pipeline error: {exc}",
                error_code="PIPELINE_ERROR", error_detail=str(exc),
            )
            self._progress.update(job_id, "error", 0, str(exc))

    def _execute(self, job_id: str, request: Dict[str, Any],
                 cancel_token: CancellationToken) -> None:
        ws = self._resolve_ws(request)
        write_log(ws, f"Pipeline started for job {job_id}")
        self._store.update(job_id, status="running", stage="prepare")

        source_lang = request.get("source_language", "auto")
        target_lang = request.get("target_language", "")
        subtitle_mode = request.get("subtitle_mode", "bilingual")
        burn = request.get("burn_subtitles", False)
        embed_soft = request.get("embed_soft_subtitles", False)
        dubbing = request.get("dubbing_enabled", False)
        low_vram_mode = bool(request.get("low_vram_mode", True))

        style_dict = request.get("style", {})
        style = SubtitleStyle.from_dict(style_dict) if style_dict else SubtitleStyle()

        # Detect stage to resume from (for retry)
        resume_stage = request.get("resume_stage", "prepare")
        if resume_stage not in _STAGE_ORDER:
            resume_stage = "prepare"
        resume_index = _STAGE_ORDER.index(resume_stage)

        def should_run(stage: str) -> bool:
            return _STAGE_ORDER.index(stage) >= resume_index

        # --- PREPARE ---
        if resume_stage in ("prepare",) and self._check_cancel(job_id, ws, cancel_token):
            return
        self._progress.update(job_id, "prepare", 0, "准备中...")
        write_log(ws, "Stage: prepare")

        source_sub = self._find_source_subtitle(ws, request)
        if not source_sub:
            self._fail(job_id, ws, "SOURCE_SUBTITLE_NOT_FOUND", "找不到源字幕文件")
            return

        req_video = request.get("source_video", "")
        source_video = Path(req_video) if req_video and Path(req_video).exists() else get_source_video(ws)

        self._progress.update(job_id, "prepare", 100, "准备完成")

        # --- NORMALIZE ---
        if resume_stage == "normalize" and self._check_cancel(job_id, ws, cancel_token):
            return
        self._store.update(job_id, stage="normalize")
        self._progress.update(job_id, "normalize", 0, "读取字幕...")
        write_log(ws, "Stage: normalize")

        segments = read_subtitle_file(source_sub)
        if not segments:
            self._fail(job_id, ws, "SUBTITLE_INVALID_TIMELINE",
                       "Unable to read source subtitle file")
            return

        raw_subtitle_dicts = [seg.to_srt_dict() for seg in segments]
        normalized_subtitle_dicts = normalize_subtitle_timeline(raw_subtitle_dicts)
        normalized_subtitle_dicts = reconstruct_split_words(normalized_subtitle_dicts)
        if len(normalized_subtitle_dicts) != len(raw_subtitle_dicts) or any(
            abs(float(normalized_subtitle_dicts[i].get("start", 0)) - float(raw_subtitle_dicts[i].get("start", 0))) > 0.001
            or abs(float(normalized_subtitle_dicts[i].get("end", 0)) - float(raw_subtitle_dicts[i].get("end", 0))) > 0.001
            or str(normalized_subtitle_dicts[i].get("text", "")) != str(raw_subtitle_dicts[i].get("text", ""))
            for i in range(min(len(normalized_subtitle_dicts), len(raw_subtitle_dicts)))
        ):
            write_log(
                ws,
                f"  Subtitle timeline normalized: {len(raw_subtitle_dicts)} -> "
                f"{len(normalized_subtitle_dicts)} segments",
            )
            segments = segments_from_srt_dicts(normalized_subtitle_dicts)

        repeated_runs = find_repeated_subtitle_runs([seg.to_srt_dict() for seg in segments])
        if repeated_runs:
            write_log(
                ws,
                "  Subtitle quality warning: repeated text runs detected "
                f"{json.dumps(repeated_runs[:5], ensure_ascii=False)}",
            )

        warnings = validate_timeline(segments)
        for w in warnings:
            write_log(ws, f"  Timeline warning [{w[0]}]: {w[1]}")

        self._progress.update(job_id, "normalize", 100, f"Loaded {len(segments)} subtitle segments")

        if low_vram_mode and dubbing and should_run("translate") and self._is_qwen3_tts_request(request):
            self._unload_qwen3_tts(ws, "before translation")

        # --- TRANSLATE ---
        if should_run("translate"):
            if self._check_cancel(job_id, ws, cancel_token):
                return
            self._store.update(job_id, stage="translate")
            self._progress.update(job_id, "translate", 0, "准备翻译...")
            write_log(ws, "Stage: translate")

            if target_lang and source_lang.lower() != target_lang.lower():
                success = self._run_translation(
                    job_id, ws, segments, request, source_lang, target_lang,
                    cancel_token,
                )
                if not success:
                    return

            self._progress.update(job_id, "translate", 100, "翻译完成")

        elif target_lang and source_lang.lower() != target_lang.lower():
            if not self._load_existing_translations(ws, segments, target_lang):
                self._fail(
                    job_id,
                    ws,
                    "TRANSLATION_ARTIFACT_NOT_FOUND",
                    "未找到可复用的翻译字幕，请从翻译阶段重新生成",
                )
                return

        if target_lang and source_lang.lower() != target_lang.lower():
            self._normalize_translation_segments(ws, segments, target_lang)
            translation_warnings = validate_translation(segments, target_lang)
            report = self._write_translation_quality_report(
                ws, segments, target_lang, translation_warnings,
            )
            if report:
                self._store.add_artifact(job_id, report)
            if dubbing and self._has_blocking_translation_warnings(translation_warnings):
                self._fail(
                    job_id, ws, "TRANSLATION_QUALITY_FAILED",
                    "翻译结果仍包含源语言残留/异常片段，已停止配音和烧录；请检查 translation_quality_report.json 后重新翻译",
                )
                return

        # --- SUBTITLE_EXPORT ---
        if should_run("subtitle_export"):
            if self._check_cancel(job_id, ws, cancel_token):
                return
            self._store.update(job_id, stage="subtitle_export")
            self._progress.update(job_id, "subtitle_export", 0, "生成字幕文件...")
            write_log(ws, "Stage: subtitle_export")

            artifacts = self._write_subtitle_files(ws, segments, subtitle_mode, source_lang, target_lang, style)
            for art in artifacts:
                self._store.add_artifact(job_id, art)

            self._progress.update(job_id, "subtitle_export", 100, "字幕生成完成")

        # --- TTS (dub mode only) ---
        if dubbing and should_run("tts"):
            if self._check_cancel(job_id, ws, cancel_token):
                return
            self._store.update(job_id, stage="tts")
            self._progress.update(job_id, "tts", 0, "TTS synthesis starting...")
            write_log(ws, "Stage: tts")

            try:
                tts_success = self._run_tts(job_id, ws, segments, request, target_lang, cancel_token)
            finally:
                if low_vram_mode and self._is_qwen3_tts_request(request):
                    self._unload_qwen3_tts(ws, "after TTS")
            if not tts_success:
                return

            self._progress.update(job_id, "tts", 100, "TTS synthesis complete")

        # --- AUDIO_MIX (dub mode only) ---
        render_video = source_video
        if dubbing and should_run("audio_mix"):
            if self._check_cancel(job_id, ws, cancel_token):
                return
            self._store.update(job_id, stage="audio_mix")
            self._progress.update(job_id, "audio_mix", 0, "音频混合...")
            write_log(ws, "Stage: audio_mix")

            mix_success = self._run_audio_mix(
                job_id, ws, segments, source_video, target_lang, request, cancel_token,
            )
            if not mix_success:
                return
            dubbed_video = ws / "rendered" / f"{ws.name}_{target_lang}_dubbed.mp4"
            if dubbed_video.exists():
                render_video = dubbed_video

        if dubbing and not should_run("audio_mix"):
            dubbed_video = ws / "rendered" / f"{ws.name}_{target_lang}_dubbed.mp4"
            if dubbed_video.exists():
                render_video = dubbed_video

        if dubbing and should_run("audio_mix"):
            self._progress.update(job_id, "audio_mix", 100, "Audio mix complete")

        # --- RENDER ---
        if self._check_cancel(job_id, ws, cancel_token):
            return
        self._store.update(job_id, stage="render")

        if burn and render_video:
            self._progress.update(job_id, "render", 0, "准备渲染...")
            write_log(ws, "Stage: render")

            sub_path = self._find_ass_for_render(ws, subtitle_mode, target_lang)
            if sub_path and sub_path.exists():
                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    self._fail(job_id, ws, "FFMPEG_NOT_FOUND", "未找到 FFmpeg")
                    return

                log_path = ws / "logs" / "ffmpeg.log"
                if dubbing:
                    output_name = f"{ws.name}_{target_lang or 'sub'}_dubbed_hardsub.mp4"
                else:
                    output_name = f"{ws.name}_{target_lang or 'sub'}.mp4"
                output_path = ws / "rendered" / output_name

                result = render_hardsub(
                    video_path=render_video,
                    subtitle_path=sub_path,
                    output_path=output_path,
                    subtitle_mode=subtitle_mode,
                    cancel_checker=lambda: cancel_token.is_cancelled(),
                    log_path=log_path,
                )

                if result.get("cancelled"):
                    self._mark_cancelled(job_id, ws)
                    return
                if not result["success"]:
                    self._fail(job_id, ws, "SUBTITLE_RENDER_FAILED",
                               result.get("error", "渲染失败"))
                    return

                self._store.add_artifact(job_id, {
                    "kind": "burned_video",
                    "path": f"rendered/{output_path.name}",
                    "language": target_lang,
                })

                if embed_soft:
                    soft_output = ws / "rendered" / f"{ws.name}_{target_lang}_soft.mp4"
                    soft_result = render_softsub(
                        video_path=render_video,
                        subtitle_path=sub_path,
                        output_path=soft_output,
                        cancel_checker=lambda: cancel_token.is_cancelled(),
                        log_path=log_path,
                    )
                    if soft_result.get("success"):
                        self._store.add_artifact(job_id, {
                            "kind": "softsub_video",
                            "path": f"rendered/{soft_output.name}",
                            "language": target_lang,
                        })

            self._progress.update(job_id, "render", 100, "渲染完成")

        # --- FINALIZE ---
        if self._check_cancel(job_id, ws, cancel_token):
            return
        self._store.update(job_id, stage="finalize")
        self._progress.update(job_id, "finalize", 0, "整理产物...")
        write_log(ws, "Stage: finalize")

        self._progress.update(job_id, "finalize", 100, "完成")
        self._store.update(
            job_id, status="completed", stage="completed",
            progress=100, message="处理完成",
        )
        write_log(ws, "Pipeline completed successfully")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _resolve_ws(self, request: Dict[str, Any]) -> Path:
        ws_dir = request.get("workspace_dir", "")
        return resolve_workspace(ws_dir)

    def _check_cancel(self, job_id: str, ws: Path,
                      cancel_token: CancellationToken) -> bool:
        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return True
        return False

    def _find_source_subtitle(self, ws: Path,
                               request: Dict[str, Any]) -> Optional[Path]:
        source_sub = request.get("source_subtitle", "")
        if source_sub and Path(source_sub).exists():
            return Path(source_sub)
        return get_source_subtitle(ws)

    def _find_ass_for_render(self, ws: Path, subtitle_mode: str,
                              target_lang: str) -> Optional[Path]:
        subs_dir = ws / "subtitles"
        if not subs_dir.exists():
            return None

        if subtitle_mode == "translated" and target_lang:
            candidates = list(subs_dir.glob(f"*{target_lang}*.ass"))
            if candidates:
                return candidates[0]

        if subtitle_mode == "bilingual":
            candidates = list(subs_dir.glob("*bilingual*.ass"))
            if candidates:
                return candidates[0]

        # Fallback to any ass file
        candidates = list(subs_dir.glob("*.ass"))
        if candidates:
            return candidates[0]

        return None

    @staticmethod
    def _is_qwen3_tts_request(request: Dict[str, Any]) -> bool:
        provider = str(request.get("tts_provider", "") or "").lower()
        return provider in {"qwen3-tts", "qwen3_tts", "qwen3"}

    @staticmethod
    def _prepare_qwen3_tts_options(options: Dict[str, Any]) -> Dict[str, Any]:
        prepared = dict(options)
        seed = prepared.get("seed")
        if seed in ("", None):
            prepared["seed"] = _QWEN3_DEFAULT_STABLE_SEED
            prepared["seed_policy"] = "default_stable"
            if prepared.get("temperature") in ("", None):
                prepared["temperature"] = 0.6
                prepared["temperature_policy"] = "default_stable"
            if prepared.get("top_p") in ("", None):
                prepared["top_p"] = 0.8
                prepared["top_p_policy"] = "default_stable"
            return prepared
        try:
            seed_int = int(seed)
        except (TypeError, ValueError):
            prepared["seed"] = _QWEN3_DEFAULT_STABLE_SEED
            prepared["seed_policy"] = "default_stable"
            if prepared.get("temperature") in ("", None):
                prepared["temperature"] = 0.6
                prepared["temperature_policy"] = "default_stable"
            if prepared.get("top_p") in ("", None):
                prepared["top_p"] = 0.8
                prepared["top_p_policy"] = "default_stable"
            return prepared
        if seed_int < 0:
            prepared.pop("seed", None)
            prepared["seed_policy"] = "random"
        else:
            prepared["seed"] = seed_int
            prepared["seed_policy"] = "explicit"
        if prepared.get("temperature") in ("", None):
            prepared["temperature"] = 0.6
            prepared["temperature_policy"] = "default_stable"
        if prepared.get("top_p") in ("", None):
            prepared["top_p"] = 0.8
            prepared["top_p_policy"] = "default_stable"
        return prepared

    def _unload_qwen3_tts(self, ws: Path, reason: str) -> None:
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:8767/models/unload",
                data=b"",
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            write_log(ws, f"  Qwen3-TTS model unloaded ({reason})")
        except Exception as exc:
            write_log(ws, f"  Qwen3-TTS unload skipped ({reason}): {exc}")

    def _run_translation(self, job_id: str, ws: Path,
                          segments: List[SubtitleSegment],
                          request: Dict[str, Any],
                          source_lang: str, target_lang: str,
                          cancel_token: CancellationToken) -> bool:
        """Execute translation with batching, checkpoints, and glossary."""
        trans_config_data = request.get("translation", {})
        trans_config = TranslationConfig.from_dict(trans_config_data) if trans_config_data else TranslationConfig()

        # Glossary
        glossary_text = ""
        glossary_path = ws / "translation" / "glossary.json"
        if glossary_path.exists():
            gloss = Gloss.load_json(glossary_path)
            glossary_text = gloss.to_prompt_text()

        # Batch
        checkpoints = CheckpointManager(ws / "translation" / "checkpoints" if (ws / "translation").exists() else ws / "translation")
        pending = checkpoints.get_pending_segments(segments)
        if not pending and any(not (seg.translation or "").strip() for seg in segments):
            write_log(ws, "  Translation checkpoint ignored: translated text is missing")
            checkpoints.clear()
            pending = checkpoints.get_pending_segments(segments)

        if not pending:
            write_log(ws, "  All segments already translated (checkpoint)")
            return True

        max_batch_items = max(1, int(getattr(trans_config, "max_batch_items", 10) or 10))
        batches = batch_segments(
            pending,
            max_chars=trans_config.max_batch_chars,
            max_items=max_batch_items,
        )

        provider_name = trans_config.provider
        try:
            get_provider(provider_name)
        except ValueError:
            self._fail(job_id, ws, "TRANSLATION_ERROR",
                       f"Unknown provider: {provider_name}")
            return False

        total = len(batches)
        completed_batches = 0
        concurrency = max(1, int(getattr(trans_config, "concurrency", 1) or 1))
        concurrency = min(concurrency, total)

        def request_translation(batch: List[SubtitleSegment]) -> List[Dict]:
            if cancel_token.is_cancelled():
                raise RuntimeError("Task cancelled")
            provider = get_provider(provider_name)
            try:
                return provider.translate_batch(
                    batch_to_request(batch), trans_config, source_lang, target_lang,
                    glossary=glossary_text,
                )
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()

        def complete_translations(batch_index: int,
                                  batch: List[SubtitleSegment],
                                  depth: int = 0) -> List[Dict]:
            try:
                translations = request_translation(batch)
            except Exception as exc:
                if cancel_token.is_cancelled():
                    raise RuntimeError("Task cancelled") from exc
                if len(batch) <= 1:
                    seg = batch[0]
                    write_log(
                        ws,
                        f"  Batch {batch_index}: segment {seg.index} translation failed; using source text ({exc})",
                    )
                    return [{"id": seg.index, "text": seg.text, "_fallback_source": True}]
                mid = max(1, len(batch) // 2)
                write_log(
                    ws,
                    f"  Batch {batch_index}: translation response invalid; splitting {len(batch)} segments",
                )
                return (
                    complete_translations(batch_index, batch[:mid], depth + 1)
                    + complete_translations(batch_index, batch[mid:], depth + 1)
                )

            batch_ids = {s.index for s in batch}
            valid_by_id = {
                int(t["id"]): self._clean_translation_text(str(t.get("text", "")))
                for t in translations
                if isinstance(t, dict)
                and str(t.get("id", "")).isdigit()
                and int(t.get("id")) in batch_ids
                and str(t.get("text", "")).strip()
            }
            fallback_ids = set()

            missing = [seg for seg in batch if seg.index not in valid_by_id]
            if missing:
                if len(batch) <= 1:
                    seg = batch[0]
                    write_log(
                        ws,
                        f"  Batch {batch_index}: segment {seg.index} missing/empty translation; using source text",
                    )
                    valid_by_id[seg.index] = seg.text
                    fallback_ids.add(seg.index)
                else:
                    write_log(
                        ws,
                        f"  Batch {batch_index}: retrying {len(missing)} missing/empty translations",
                    )
                    for item in complete_translations(batch_index, missing, depth + 1):
                        if str(item.get("text", "")).strip():
                            item_id = int(item["id"])
                            valid_by_id[item_id] = self._clean_translation_text(str(item["text"]))
                            if item.get("_fallback_source") or item.get("_quality_failed"):
                                fallback_ids.add(item_id)

            result = []
            for seg in batch:
                text = valid_by_id.get(seg.index, seg.text)
                item = {"id": seg.index, "text": text}
                if seg.index in fallback_ids:
                    item["_fallback_source"] = True
                result.append(item)

            source_by_id = {seg.index: seg.text for seg in batch}
            issues_by_id = validate_translation_items(result, source_by_id, target_lang)
            bad_ids = {
                item_id
                for item_id, issues in issues_by_id.items()
                if has_blocking_issues(issues)
            }

            if bad_ids and depth < 3 and len(batch) > 1:
                bad_batch = [seg for seg in batch if seg.index in bad_ids]
                write_log(
                    ws,
                    f"  Batch {batch_index}: retrying {len(bad_batch)} target-language quality failures",
                )
                for item in complete_translations(batch_index, bad_batch, depth + 1):
                    item_id = int(item["id"])
                    valid_by_id[item_id] = self._clean_translation_text(str(item.get("text", "")))
                    if item.get("_fallback_source") or item.get("_quality_failed"):
                        fallback_ids.add(item_id)
                    else:
                        fallback_ids.discard(item_id)
                result = []
                for seg in batch:
                    text = valid_by_id.get(seg.index, seg.text)
                    item = {"id": seg.index, "text": text}
                    if seg.index in fallback_ids:
                        item["_fallback_source"] = True
                    result.append(item)
                issues_by_id = validate_translation_items(result, source_by_id, target_lang)
                bad_ids = {
                    item_id
                    for item_id, issues in issues_by_id.items()
                    if has_blocking_issues(issues)
                }

            if bad_ids:
                for item in result:
                    item_id = int(item["id"])
                    if item_id in bad_ids:
                        joined = "; ".join(issue.code for issue in issues_by_id.get(item_id, []))
                        item["_quality_failed"] = joined or "TARGET_LANGUAGE_QUALITY_FAILED"
                        item["_fallback_source"] = True
                        write_log(
                            ws,
                            f"  Batch {batch_index}: segment {item_id} failed target-language check: {item['_quality_failed']}",
                        )

            return result

        def translate_one(batch_index: int,
                          batch: List[SubtitleSegment]) -> tuple[int, List[SubtitleSegment], List[Dict]]:
            return batch_index, batch, complete_translations(batch_index, batch)

        if concurrency > 1:
            write_log(ws, f"  Translation concurrency: {concurrency}")
            self._progress.update(
                job_id, "translate", 0,
                f"Requesting translation API with {concurrency} threads...",
            )

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(translate_one, idx, batch): idx
                    for idx, batch in enumerate(batches, 1)
                }
                for future in as_completed(futures):
                    if cancel_token.is_cancelled():
                        self._mark_cancelled(job_id, ws)
                        for pending_future in futures:
                            pending_future.cancel()
                        return False
                    try:
                        batch_index, batch, translations = future.result()
                    except Exception as e:
                        for pending_future in futures:
                            pending_future.cancel()
                        if cancel_token.is_cancelled():
                            self._mark_cancelled(job_id, ws)
                        else:
                            self._fail(job_id, ws, "TRANSLATION_ERROR", str(e)[:200])
                        return False

                    if self._has_source_fallback_translations(batch, translations, request):
                        self._fail(job_id, ws, "TRANSLATION_INCOMPLETE",
                                   "翻译结果不完整或目标语言校验失败，已停止配音以避免把源语言/脏字幕送入 TTS")
                        for pending_future in futures:
                            pending_future.cancel()
                        return False
                    self._apply_translation_result(batch, translations)
                    checkpoints.mark_completed([s.index for s in batch])
                    write_log(ws, f"  Batch {batch_index}/{total} translated ({len(batch)} segments)")
                    completed_batches += 1
                    self._progress.update(
                        job_id, "translate", int((completed_batches / total) * 100),
                        f"Translated batch {completed_batches}/{total}",
                    )

            return True

        for batch_index, batch in enumerate(batches, 1):
            if cancel_token.is_cancelled():
                self._mark_cancelled(job_id, ws)
                return False

            batch_pct = int((completed_batches / total) * 100)
            self._progress.update(
                job_id, "translate", batch_pct,
                f"Requesting translation API: batch {batch_index}/{total}",
            )

            try:
                _, batch, translations = translate_one(batch_index, batch)
            except Exception as e:
                if cancel_token.is_cancelled():
                    self._mark_cancelled(job_id, ws)
                else:
                    self._fail(job_id, ws, "TRANSLATION_ERROR", str(e)[:200])
                return False

            if self._has_source_fallback_translations(batch, translations, request):
                self._fail(job_id, ws, "TRANSLATION_INCOMPLETE",
                           "翻译结果不完整或目标语言校验失败，已停止配音以避免把源语言/脏字幕送入 TTS")
                return False
            self._apply_translation_result(batch, translations)
            checkpoints.mark_completed([s.index for s in batch])
            write_log(ws, f"  Batch {batch_index}/{total} translated ({len(batch)} segments)")
            completed_batches += 1
            self._progress.update(
                job_id, "translate", int((completed_batches / total) * 100),
                f"Translated batch {completed_batches}/{total}",
            )

        return True

    @staticmethod
    def _has_source_fallback_translations(batch: List[SubtitleSegment],
                                          translations: List[Dict],
                                          request: Dict[str, Any]) -> bool:
        if not bool(request.get("dubbing_enabled", False)):
            return False
        fallback_ids = {
            int(t["id"])
            for t in translations
            if isinstance(t, dict)
            and str(t.get("id", "")).isdigit()
            and (t.get("_fallback_source") or t.get("_quality_failed"))
        }
        if fallback_ids:
            return True
        trans_map = {
            int(t["id"]): str(t.get("text", "")).strip()
            for t in translations
            if isinstance(t, dict) and str(t.get("id", "")).isdigit()
        }
        for seg in batch:
            translated = trans_map.get(seg.index, "")
            if not translated:
                return True
        return False

    def _apply_translation_result(self, batch: List[SubtitleSegment],
                                  translations: List[Dict]) -> None:
        trans_map = {t["id"]: t["text"] for t in translations}
        for seg in batch:
            if seg.index in trans_map:
                seg.translation = self._clean_translation_text(trans_map[seg.index])

    @staticmethod
    def _clean_translation_text(text: str) -> str:
        cleaned = str(text or "").strip()
        replacements = {
            "\u0104\u0141": "?",
            "\u0104\u02d8": "?",
            "\u9225": "'",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        return cleaned

    def _load_existing_translations(self, ws: Path,
                                    segments: List[SubtitleSegment],
                                    target_lang: str) -> bool:
        subs_dir = ws / "subtitles"
        candidates = [
            subs_dir / f"{target_lang}.srt",
            subs_dir / f"{target_lang.lower()}.srt",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                translated_segments = read_subtitle_file(path)
            except Exception as exc:
                write_log(ws, f"  Failed to load translated subtitles from {path.name}: {exc}")
                continue
            by_index = {
                seg.index: self._clean_translation_text(seg.text)
                for seg in translated_segments
                if (seg.text or "").strip()
            }
            if not by_index:
                continue
            for seg in segments:
                if seg.index in by_index:
                    seg.translation = by_index[seg.index]
            missing = [seg.index for seg in segments if not (seg.translation or "").strip()]
            if missing:
                write_log(ws, f"  Existing translation missing {len(missing)} segments")
                return False
            write_log(ws, f"  Loaded existing translations from {path.name}")
            return True
        return False

    def _write_subtitle_files(self, ws: Path,
                               segments: List[SubtitleSegment],
                               subtitle_mode: str,
                               source_lang: str,
                               target_lang: str,
                               style: SubtitleStyle) -> List[Dict]:
        """Write all subtitle variants and return artifact metadata."""
        artifacts: List[Dict] = []
        subs_dir = ws / "subtitles"
        subs_dir.mkdir(parents=True, exist_ok=True)

        # Source SRT
        src_srt = subs_dir / f"source_{source_lang}.srt"
        write_srt(segments, src_srt, mode="source")
        artifacts.append({"kind": "source_srt", "path": f"subtitles/{src_srt.name}",
                          "language": source_lang})

        # Translated SRT
        if target_lang and subtitle_mode in ("translated", "bilingual"):
            trans_srt = subs_dir / f"{target_lang}.srt"
            write_srt(segments, trans_srt, mode="translated")
            artifacts.append({"kind": "translated_srt", "path": f"subtitles/{trans_srt.name}",
                              "language": target_lang})

        # Bilingual SRT
        if subtitle_mode == "bilingual" and target_lang:
            bi_srt = subs_dir / f"bilingual_{target_lang}.srt"
            write_srt(segments, bi_srt, mode="bilingual")
            artifacts.append({"kind": "bilingual_srt", "path": f"subtitles/{bi_srt.name}",
                              "language": target_lang})

        # Source ASS
        try:
            src_ass_content = segments_to_ass(segments, style, mode="source")
            src_ass = subs_dir / f"source_{source_lang}.ass"
            save_ass(src_ass_content, src_ass)
            artifacts.append({"kind": "source_ass", "path": f"subtitles/{src_ass.name}",
                              "language": source_lang})
        except Exception:
            pass

        # Translated ASS
        if target_lang and subtitle_mode in ("translated", "bilingual"):
            try:
                trans_ass_content = segments_to_ass(segments, style, mode="translated")
                trans_ass = subs_dir / f"{target_lang}.ass"
                save_ass(trans_ass_content, trans_ass)
                artifacts.append({"kind": "translated_ass",
                                  "path": f"subtitles/{trans_ass.name}",
                                  "language": target_lang})
            except Exception:
                pass

        # Bilingual ASS
        if subtitle_mode == "bilingual" and target_lang:
            try:
                bi_ass_content = segments_to_ass(segments, style, mode="bilingual")
                bi_ass = subs_dir / f"bilingual_{target_lang}.ass"
                save_ass(bi_ass_content, bi_ass)
                artifacts.append({"kind": "bilingual_ass",
                                  "path": f"subtitles/{bi_ass.name}",
                                  "language": target_lang})
            except Exception:
                pass

        return artifacts

    def _build_voice_profile(self, request: Dict[str, Any],
                              target_lang: str) -> Optional[Dict]:
        """Build a frozen voice profile snapshot from request options.

        Returns dict with 'profile' (TtsVoiceProfile) and 'hash' (str),
        or None if voice_profile module unavailable.
        """
        if not _HAS_VOICE_PROFILE:
            return None

        tts_options = request.get("tts_options", {}) or {}
        tts_provider_name = request.get("tts_provider", "edge-tts")
        tts_voice = request.get("tts_voice", "")
        if not tts_voice:
            tts_voice = self._default_tts_voice(target_lang)

        consistency_mode = str(
            request.get("tts_consistency_mode",
                        tts_options.get("tts_consistency_mode", "stable"))
            or "stable"
        ).lower()
        if consistency_mode not in ("fast", "stable", "strict"):
            consistency_mode = "stable"

        model = str(
            tts_options.get("qwen_model", "")
            or tts_options.get("model", "")
            or os.environ.get("V2S_QWEN3_TTS_MODEL", "")
            or ""
        )
        profile = TtsVoiceProfile(
            provider=tts_provider_name,
            model=model,
            voice=tts_voice,
            prompt_audio_path=str(tts_options.get("ref_audio", "") or "").strip() or None,
            prompt_audio_hash=str(tts_options.get("ref_audio", "") or "").strip() or None,
            language=target_lang,
            style=str(tts_options.get("instruct", "") or "").strip() or None,
            seed=tts_options.get("seed"),
            temperature=tts_options.get("temperature"),
            top_p=tts_options.get("top_p"),
            sample_rate=24000,
            consistency_mode=consistency_mode,
        )
        profile_hash = voice_profile_hash(profile)
        return {"profile": profile, "hash": profile_hash,
                "consistency_mode": consistency_mode}

    def _run_tts(self, job_id: str, ws: Path,
                  segments: List[SubtitleSegment],
                  request: Dict[str, Any],
                  target_lang: str,
                  cancel_token: CancellationToken) -> bool:
        """Synthesize TTS audio for each translated segment."""
        tts_dir = ws / "audio" / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        tts_provider_name = request.get("tts_provider", "edge-tts")
        tts_voice = request.get("tts_voice", "")
        tts_options = dict(request.get("tts_options", {}) or {})
        if not tts_voice:
            tts_voice = self._default_tts_voice(target_lang)

        import json as _json_module

        voice_profile_info = self._build_voice_profile(request, target_lang)
        consistency_mode = "fast"
        profile_hash = ""
        if voice_profile_info is not None:
            consistency_mode = voice_profile_info["consistency_mode"]
            profile_hash = voice_profile_info["hash"]
            log_profile = profile_to_log_dict(
                voice_profile_info["profile"], profile_hash
            )
            write_log(ws, f"  Voice profile frozen: {_json_module.dumps(log_profile, ensure_ascii=False)}")

        try:
            from tts import get_provider as get_tts_provider
            provider = get_tts_provider(tts_provider_name, cache_dir=tts_dir)
        except (ImportError, ValueError) as e:
            self._fail(job_id, ws, "TTS_UNAVAILABLE",
                       f"TTS provider {tts_provider_name} unavailable: {e}")
            return False
        try:
            from tts.base import TTSAuthError
        except Exception:
            TTSAuthError = None  # type: ignore[assignment]

        from tts.timing import adjust_timing, extract_audio_window, save_timing_report

        # Preflight: ensure Qwen3-TTS sidecar is running before attempting synthesis
        if self._is_qwen3_tts_request(request):
            try:
                import json as _json
                import urllib.request as _urllib
                _req = _urllib.Request("http://127.0.0.1:8767/health", method="GET")
                with _urllib.urlopen(_req, timeout=3) as _resp:
                    _data = _json.loads(_resp.read().decode("utf-8"))
                if _data.get("status") != "ok":
                    raise RuntimeError(f"health check returned: {_data.get('status')}")
            except Exception as _exc:
                write_log(ws, f"  Qwen3-TTS preflight failed: {_exc}")
                self._fail(job_id, ws, "TTS_SERVICE_DOWN",
                           "Qwen3-TTS 本地服务未运行，请在 设置 → Qwen3-TTS 管理 中点击「启动服务」后重试")
                return False

            # Voice-language compatibility preflight.  Using a voice whose
            # native language differs from the target language (e.g. Korean
            # "Sohee" for Chinese text) makes Qwen3-TTS produce abnormally
            # long, accented, or off-language speech, which then gets
            # hard-clipped and sounds like swallowed/incorrect audio.
            try:
                from tts.qwen3_tts import (
                    is_voice_compatible,
                    compatible_voice_for,
                    voice_language,
                    QWEN3_VOICE_LANGUAGE_MAP,
                )
            except ImportError:
                is_voice_compatible = None  # type: ignore[assignment]
            if callable(is_voice_compatible) and tts_voice and target_lang:
                if not is_voice_compatible(tts_voice, target_lang):
                    native_lang = voice_language(tts_voice) if callable(voice_language) else "?"
                    corrected_voice = compatible_voice_for(target_lang, tts_voice) if callable(compatible_voice_for) else tts_voice
                    write_log(
                        ws,
                        f"  TTS voice-language mismatch: voice '{tts_voice}' is native "
                        f"for '{native_lang}' but target language is '{target_lang}'. "
                        f"Auto-correcting to '{corrected_voice}' to avoid off-language "
                        f"speech and severe hard-clipping.",
                    )
                    tts_voice = corrected_voice

        tts_segments: List[Tuple[int, Path, float]] = []
        speed_ratios = {}
        candidates = []
        skipped_tts_inputs: List[Dict[str, Any]] = []
        source_lang = str(request.get("source_language", "") or "").strip().lower()
        translation_required_for_tts = bool(
            target_lang
            and source_lang
            and source_lang not in {"auto", "unknown"}
            and source_lang != str(target_lang).strip().lower()
        )
        missing_tts_translations: List[int] = []
        for seg in segments:
            if seg.translation:
                raw_text = seg.translation
            elif translation_required_for_tts and is_speech_subtitle_text(seg.text):
                missing_tts_translations.append(seg.index)
                continue
            else:
                raw_text = seg.text or ""
            clean_text = normalize_subtitle_text(raw_text)
            if not is_speech_subtitle_text(clean_text):
                if clean_text:
                    skipped_tts_inputs.append({
                        "index": seg.index,
                        "start": seg.start,
                        "end": seg.end,
                        "text": clean_text,
                        "reason": "non_speech_text",
                    })
                continue
            if seg.translation:
                candidates.append(replace(seg, translation=clean_text))
            else:
                candidates.append(replace(seg, text=clean_text))
        if missing_tts_translations:
            self._fail(
                job_id, ws, "TTS_TRANSLATION_MISSING",
                f"有 {len(missing_tts_translations)} 条字幕缺少目标语言翻译，已停止配音，避免把源语言文本送入 TTS",
            )
            return False
        candidates.sort(key=lambda seg: (seg.start, seg.index))
        total = len(candidates)
        completed = 0
        tts_concurrency = max(1, int(request.get("tts_concurrency", 1) or 1))
        tts_concurrency = min(tts_concurrency, 4, total or 1)
        if not getattr(provider, "supports_concurrency", True):
            tts_concurrency = 1
        try:
            tts_gap = float(tts_options.get("tts_segment_gap", request.get("tts_segment_gap", 0.04)) or 0.04)
        except (TypeError, ValueError):
            tts_gap = 0.04
        tts_gap = max(0.0, min(0.25, tts_gap))
        min_tts_duration = 0.02

        # Filled after the timeline-aware planner has calculated each segment's
        # safe tolerance/budget. Keeping this here documents the data shape for
        # the nested synthesis helpers below.
        target_durations: Dict[int, float] = {}

        write_log(ws, f"  TTS engine: {tts_provider_name}, voice: {tts_voice}, language: {target_lang}")
        write_log(ws, f"  TTS segments: {total}, concurrency: {tts_concurrency}")
        if skipped_tts_inputs:
            write_log(
                ws,
                f"  TTS skipped {len(skipped_tts_inputs)} non-speech subtitle inputs "
                "(punctuation/filler only)",
            )
        write_log(ws, f"  TTS output_dir: {tts_dir}")

        options = {
            "rate": "+0%",
            "pitch": "+0Hz",
            "volume": "+0%",
            "timeout": 60,
        }
        options.update(tts_options)
        if tts_provider_name in ("qwen3-tts", "qwen3_tts", "qwen3"):
            options = self._prepare_qwen3_tts_options(options)
            prompt_id = self._ensure_qwen3_clone_prompt(provider, options, ws)
            if prompt_id:
                options["voice_clone_prompt_id"] = prompt_id

        report_rows: List[Dict[str, Any]] = []
        tts_errors: List[Dict[str, Any]] = []
        chunk_fallback_count = 0

        def _float_option(*keys: str, default: float, minimum: float, maximum: float) -> float:
            value: Any = default
            for key in keys:
                if key in tts_options:
                    value = tts_options.get(key)
                    break
                if key in request:
                    value = request.get(key)
                    break
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(maximum, parsed))

        def _int_option(*keys: str, default: int, minimum: int, maximum: int) -> int:
            value: Any = default
            for key in keys:
                if key in tts_options:
                    value = tts_options.get(key)
                    break
                if key in request:
                    value = request.get(key)
                    break
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(maximum, parsed))

        def _bool_option(*keys: str, default: bool) -> bool:
            value: Any = default
            for key in keys:
                if key in tts_options:
                    value = tts_options.get(key)
                    break
                if key in request:
                    value = request.get(key)
                    break
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
            return bool(value)

        chunk_options = {
            "max_chars": _int_option("tts_chunk_max_chars", "max_chars", default=500, minimum=20, maximum=5000),
            "max_duration_sec": _float_option(
                "tts_chunk_max_duration_sec", "max_duration_sec",
                default=45.0, minimum=1.0, maximum=120.0,
            ),
            "min_chars": _int_option("tts_chunk_min_chars", "min_chars", default=40, minimum=1, maximum=1000),
            "prefer_sentence_end": _bool_option(
                "tts_chunk_prefer_sentence_end", "prefer_sentence_end", default=True,
            ),
            "max_gap_sec": _float_option(
                "tts_chunk_max_gap_sec", "max_gap_sec",
                default=0.8, minimum=0.1, maximum=5.0,
            ),
            "max_timeline_span_sec": _float_option(
                "tts_chunk_max_timeline_span_sec", "max_timeline_span_sec",
                default=12.0, minimum=2.0, maximum=60.0,
            ),
            "max_tolerance_sec": _float_option(
                "tts_chunk_max_tolerance_sec", "max_tolerance_sec",
                default=0.8, minimum=0.0, maximum=5.0,
            ),
            "soft_speed_factor": _float_option(
                "tts_soft_speed_factor", "soft_speed_factor",
                default=1.15, minimum=1.0, maximum=2.5,
            ),
            "max_speed_factor": _float_option(
                "tts_max_speed_factor", "max_speed_factor",
                default=1.5, minimum=1.0, maximum=3.0,
            ),
            "max_chunk_speed_factor": _float_option(
                "tts_max_chunk_speed_factor", "max_chunk_speed_factor",
                default=1.35, minimum=1.0, maximum=3.0,
            ),
            "allow_proportional_chunk_split": _bool_option(
                "tts_chunk_allow_proportional_split",
                "allow_proportional_chunk_split",
                default=False,
            ),
        }
        chunk_max_gap_sec = float(chunk_options["max_gap_sec"])
        allow_proportional_chunk_split = bool(chunk_options["allow_proportional_chunk_split"])
        tts_hard_clip = _bool_option("tts_hard_clip", "hard_clip_tts", default=True)
        tts_hard_clip_overflow_sec = _float_option(
            "tts_hard_clip_overflow_sec", "max_overflow_sec",
            default=0.08, minimum=0.0, maximum=1.0,
        )
        duration_budget_mode = str(
            tts_options.get(
                "tts_duration_budget_mode",
                request.get("tts_duration_budget_mode", "borrow_gap"),
            ) or "borrow_gap"
        ).strip().lower()

        tts_plan = build_tts_plan(candidates, chunk_options) if callable(build_tts_plan) else None
        plan_segments_by_index: Dict[int, Dict[str, Any]] = {}
        plan_chunks_by_index: Dict[int, Dict[str, Any]] = {}
        if tts_plan is not None:
            plan_segments_by_index = {
                int(item.index): item.to_dict()
                for item in getattr(tts_plan, "segments", [])
            }
            plan_chunks_by_index = {
                int(item.chunk_index): item.to_dict()
                for item in getattr(tts_plan, "chunks", [])
            }

        def _target_duration_for_segment(i: int, seg: SubtitleSegment) -> float:
            """Return the safe speech window for one subtitle segment.

            The useful VideoLingo-inspired idea here is to plan the dubbing
            budget before synthesis: a line may borrow a small following gap,
            but it must not bleed into the next subtitle. This keeps TTS output
            from being globally over-compressed while preserving subtitle starts.
            """
            nominal = max(min_tts_duration, float(seg.end) - float(seg.start))
            next_start = float(candidates[i + 1].start) if i + 1 < len(candidates) else None
            max_safe = nominal
            if next_start is not None:
                max_safe = max(min_tts_duration, next_start - float(seg.start) - tts_gap)

            seg_plan = plan_segments_by_index.get(int(seg.index), {})
            planned_available = seg_plan.get("available_duration")
            planned_estimated = seg_plan.get("estimated_duration")
            try:
                available_budget = float(planned_available)
            except (TypeError, ValueError):
                available_budget = nominal
            try:
                estimated_budget = float(planned_estimated)
            except (TypeError, ValueError):
                estimated_budget = nominal

            if duration_budget_mode in {"strict", "subtitle", "subtitle_only"}:
                desired = nominal
            elif duration_budget_mode in {"estimate", "speech_estimate"}:
                desired = max(nominal * 0.65, min(available_budget, estimated_budget))
            else:
                # Default: borrow only the planner-approved tolerance from a
                # short following gap. Long visual pauses still split chunks and
                # remain silent instead of being swallowed by TTS.
                desired = max(nominal, available_budget)

            if next_start is not None:
                desired = min(desired, max_safe)
            return max(min_tts_duration, desired)

        target_durations = {
            int(seg.index): _target_duration_for_segment(i, seg)
            for i, seg in enumerate(candidates)
        }
        if candidates:
            write_log(
                ws,
                f"  TTS duration budget: mode={duration_budget_mode}, "
                f"hard_clip={tts_hard_clip}, overflow={tts_hard_clip_overflow_sec:.2f}s",
            )

        segment_by_index = {seg.index: seg for seg in candidates}
        next_start_by_index: Dict[int, Optional[float]] = {}
        for i, seg in enumerate(candidates):
            next_start_by_index[seg.index] = candidates[i + 1].start if i + 1 < len(candidates) else None

        # ------------------------------------------------------------------
        # Build TTS chunks for stable/strict modes (merge only local segments)
        # ------------------------------------------------------------------
        use_chunking = consistency_mode in ("stable", "strict") and _HAS_VOICE_PROFILE and callable(build_tts_chunks)
        chunks: List[Any] = []
        chunk_to_segments: Dict[int, List[SubtitleSegment]] = {}

        if use_chunking:
            raw_chunks = build_tts_chunks(candidates, chunk_options)
            pressure_summary: Dict[str, int] = {}
            for item in plan_segments_by_index.values():
                pressure = str(item.get("speed_pressure") or "unknown")
                pressure_summary[pressure] = pressure_summary.get(pressure, 0) + 1
            write_log(
                ws,
                f"  TTS chunks: {len(candidates)} segments -> {len(raw_chunks)} chunks "
                f"(mode={consistency_mode}, max_gap={chunk_options['max_gap_sec']}s, "
                f"max_span={chunk_options['max_timeline_span_sec']}s, "
                f"allow_proportional_split={allow_proportional_chunk_split}, "
                f"pressure={pressure_summary})",
            )
            for c in raw_chunks:
                c_segs = [seg for seg in candidates if seg.index in c.segment_indexes]
                chunk_to_segments[c.chunk_index] = c_segs
                chunks.append(c)
        else:
            write_log(ws, f"  TTS mode: per-segment (consistency={consistency_mode})")

        # ------------------------------------------------------------------
        # Shared helpers
        # ------------------------------------------------------------------
        def _timeline_fields(
            seg: SubtitleSegment,
            *,
            source_mode: str,
            chunk_index: Optional[int] = None,
            chunk_segment_indexes: Optional[List[int]] = None,
            source_window_start: Optional[float] = None,
            source_window_duration: Optional[float] = None,
        ) -> Dict[str, Any]:
            next_start = next_start_by_index.get(seg.index)
            gap_to_next = None if next_start is None else max(0.0, float(next_start) - float(seg.end))
            seg_plan = plan_segments_by_index.get(int(seg.index), {})
            chunk_plan = plan_chunks_by_index.get(int(chunk_index)) if chunk_index is not None else None
            return {
                "subtitle_start": float(seg.start),
                "subtitle_end": float(seg.end),
                "next_subtitle_start": next_start,
                "subtitle_gap_to_next": gap_to_next,
                "source_mode": source_mode,
                "chunk_index": chunk_index,
                "chunk_segment_indexes": chunk_segment_indexes or [],
                "source_window_start": source_window_start,
                "source_window_duration": source_window_duration,
                "overlay_start": float(seg.start),
                "overlay_start_matches_subtitle_start": True,
                "planned_tolerance": seg_plan.get("tolerance"),
                "planned_available_duration": seg_plan.get("available_duration"),
                "planned_estimated_duration": seg_plan.get("estimated_duration"),
                "planned_target_duration": target_durations.get(int(seg.index)),
                "duration_budget_mode": duration_budget_mode,
                "planned_speed_factor": seg_plan.get("speed_factor"),
                "speed_pressure": seg_plan.get("speed_pressure"),
                "chunk_speed_factor": (chunk_plan or {}).get("speed_factor"),
                "chunk_speed_pressure": (chunk_plan or {}).get("speed_pressure"),
                "chunk_keep_gaps": (chunk_plan or {}).get("keep_gaps"),
                "chunk_split_reason": (chunk_plan or {}).get("split_reason"),
            }

        def record_report(result: Dict[str, Any]) -> None:
            if not result.get("index"):
                return
            seg = segment_by_index.get(int(result.get("index")))
            start = result.get("start")
            overlay_matches = True
            if seg is not None and start is not None:
                try:
                    overlay_matches = abs(float(start) - float(seg.start)) <= 0.05
                except (TypeError, ValueError):
                    overlay_matches = False
            report_rows.append({
                "index": result.get("index"),
                "start": result.get("start"),
                "subtitle_start": result.get("subtitle_start"),
                "subtitle_end": result.get("subtitle_end"),
                "next_subtitle_start": result.get("next_subtitle_start"),
                "subtitle_gap_to_next": result.get("subtitle_gap_to_next"),
                "source_mode": result.get("source_mode", "per_segment"),
                "chunk_index": result.get("chunk_index"),
                "chunk_segment_indexes": result.get("chunk_segment_indexes", []),
                "source_window_start": result.get("source_window_start"),
                "source_window_duration": result.get("source_window_duration"),
                "overlay_start": start,
                "overlay_start_matches_subtitle_start": overlay_matches,
                "planned_tolerance": result.get("planned_tolerance"),
                "planned_available_duration": result.get("planned_available_duration"),
                "planned_estimated_duration": result.get("planned_estimated_duration"),
                "planned_target_duration": result.get("planned_target_duration"),
                "duration_budget_mode": result.get("duration_budget_mode"),
                "planned_speed_factor": result.get("planned_speed_factor"),
                "speed_pressure": result.get("speed_pressure"),
                "chunk_speed_factor": result.get("chunk_speed_factor"),
                "chunk_speed_pressure": result.get("chunk_speed_pressure"),
                "chunk_keep_gaps": result.get("chunk_keep_gaps"),
                "chunk_split_reason": result.get("chunk_split_reason"),
                "mode": result.get("mode", options.get("qwen_mode", "")),
                "cached": bool(result.get("cached", False)),
                "target_duration": result.get("target_duration"),
                "actual_duration": result.get("actual_duration"),
                "adjusted_duration": result.get("adjusted_duration"),
                "speed": result.get("speed"),
                "warning": result.get("warning", ""),
                "error": result.get("error", ""),
                "skipped": bool(result.get("skipped", False)),
                "reason": result.get("reason", ""),
            })

        def _synthesize_and_time(seg: SubtitleSegment, text: str,
                                 temp_path: Path, seg_path: Path,
                                 target_dur: float) -> Dict[str, Any]:
            try:
                result = provider.synthesize(
                    text.strip(), target_lang, tts_voice, temp_path, options,
                )
                if cancel_token.is_cancelled():
                    seg_path.unlink(missing_ok=True)
                    temp_path.unlink(missing_ok=True)
                    return {"cancelled": True}
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                write_log(ws,
                    f"  TTS segment failed | index={seg.index} "
                    f"start={seg.start} end={seg.end} "
                    f"text_preview={text.strip()[:100]} "
                    f"error={e}")
                write_log(ws, f"  TTS traceback:\n{tb}")
                seg_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True)
                error_type = "auth" if TTSAuthError and isinstance(e, TTSAuthError) else "generation"
                return {"error": str(e), "index": seg.index, "start": seg.start,
                        "target_duration": target_dur, "error_type": error_type}

            actual_dur = result.duration_seconds
            speed = None
            warning = ""

            # Detect abnormally long TTS output early.  A voice-language
            # mismatch (e.g. Korean voice reading Chinese) often makes the
            # model produce 2-5x longer audio than estimated, which leads to
            # severe hard-clipping and "swallowed sound" artifacts.
            seg_plan = plan_segments_by_index.get(int(seg.index), {})
            try:
                estimated_dur = float(seg_plan.get("estimated_duration") or 0.0)
            except (TypeError, ValueError):
                estimated_dur = 0.0
            if actual_dur > 0 and estimated_dur > 0 and actual_dur > estimated_dur * 2.0:
                write_log(
                    ws,
                    f"  TTS abnormal duration | index={seg.index} "
                    f"actual={actual_dur:.2f}s estimated={estimated_dur:.2f}s "
                    f"ratio={actual_dur / estimated_dur:.1f}x — possible voice-language "
                    f"mismatch or model issue; text_preview={text.strip()[:80]}",
                )
                warning = (
                    f"abnormal_duration: actual {actual_dur:.2f}s is "
                    f"{actual_dur / estimated_dur:.1f}x longer than estimated "
                    f"{estimated_dur:.2f}s"
                )

            result_path = normalize_tts_audio(temp_path) if callable(normalize_tts_audio) else None
            normalized_path = Path(result_path) if result_path else temp_path

            if actual_dur > 0 and target_dur > 0:
                adj_dur, warning, speed = _adjust_timing_for_window(
                    normalized_path, seg_path, actual_dur, target_dur,
                )
                if not seg_path.exists():
                    shutil.copy2(str(normalized_path), str(seg_path))
                # Warn prominently when hard-clipping removes a large portion
                # of the speech, which is the direct cause of "吞音".
                if actual_dur > 0 and adj_dur > 0:
                    post_speed_dur = actual_dur / (speed or 1.0)
                    clipped_sec = post_speed_dur - adj_dur
                    if clipped_sec > 0.25 and clipped_sec / post_speed_dur > 0.25:
                        write_log(
                            ws,
                            f"  TTS severe clipping | index={seg.index} "
                            f"actual={actual_dur:.2f}s speed={speed or 1.0:.2f}x "
                            f"post_speed={post_speed_dur:.2f}s "
                            f"target={target_dur:.2f}s "
                            f"clipped={clipped_sec:.2f}s ({clipped_sec / post_speed_dur * 100:.0f}%) — "
                            f"speech truncated, audio will not match subtitle",
                        )
            else:
                shutil.copy2(str(normalized_path), str(seg_path))

            payload = {
                "index": seg.index,
                "path": seg_path,
                "start": seg.start,
                "target_duration": target_dur,
                "actual_duration": actual_dur,
                "adjusted_duration": adj_dur if actual_dur > 0 and target_dur > 0 else actual_dur,
                "speed": speed,
                "warning": warning,
                "cached": getattr(result, "cached", False),
                "mode": getattr(result, "mode", options.get("qwen_mode", "")),
            }
            payload.update(_timeline_fields(seg, source_mode="per_segment"))
            return payload

        _wav_dur_cache: Dict[str, float] = {}


        def _adjust_timing_for_window(input_audio: Path, output_audio: Path,
                                      actual_duration: float,
                                      target_duration: float) -> Tuple[float, str, float]:
            try:
                return adjust_timing(
                    input_audio, output_audio, actual_duration, target_duration,
                    hard_clip=tts_hard_clip,
                    max_overflow_sec=tts_hard_clip_overflow_sec,
                )
            except TypeError as exc:
                # Backward compatibility for tests/plugins that monkeypatch an
                # older adjust_timing signature without hard_clip kwargs.
                if "unexpected keyword" not in str(exc):
                    raise
                return adjust_timing(input_audio, output_audio, actual_duration, target_duration)

        def _get_wav_duration_cached(path: Path) -> float:
            key = str(path.resolve())
            if key not in _wav_dur_cache:
                from tts.qwen3_tts import _get_wav_duration
                _wav_dur_cache[key] = _get_wav_duration(path)
            return _wav_dur_cache[key]

        def _max_segment_gap(csegs: List[SubtitleSegment]) -> float:
            if len(csegs) < 2:
                return 0.0
            ordered = sorted(csegs, key=lambda item: (item.start, item.index))
            gaps = [
                float(ordered[i + 1].start) - float(ordered[i].end)
                for i in range(len(ordered) - 1)
            ]
            return max([0.0] + gaps)

        def _build_chunk_windows(csegs: List[SubtitleSegment],
                                 chunk_dur: float,
                                 chunk_path: Path) -> Optional[Dict[int, Tuple[float, float]]]:
            """Build segment windows using silence detection with guarded fallback.

            Character-proportion slicing is only safe for a compact subtitle
            region. If a chunk somehow crosses a long timeline gap, returning
            None forces per-segment synthesis instead of putting later speech
            into an earlier subtitle window.
            """
            if chunk_dur <= 0:
                return {}

            from tts.timing import detect_silence_boundaries
            boundaries = detect_silence_boundaries(chunk_path)
            target_boundary_count = len(csegs) - 1

            if len(boundaries) >= target_boundary_count and target_boundary_count >= 1:
                # Prefer real silence boundaries closest to the text-proportional
                # expected split points.  This is safer than taking evenly spaced
                # silences when the TTS model inserts extra pauses inside a line.
                if len(boundaries) > target_boundary_count:
                    text_units = [
                        max(1, len((seg.translation or seg.text or "").strip()))
                        for seg in csegs
                    ]
                    total_units = max(1, sum(text_units))
                    expected_points: List[float] = []
                    cursor_units = 0
                    for units in text_units[:-1]:
                        cursor_units += units
                        expected_points.append(chunk_dur * (cursor_units / total_units))

                    picked = []
                    remaining = list(boundaries)
                    lower_bound = 0.0
                    for expected in expected_points:
                        viable = [point for point in remaining if point > lower_bound + 0.03]
                        if not viable:
                            break
                        chosen = min(viable, key=lambda point: abs(point - expected))
                        picked.append(chosen)
                        lower_bound = chosen
                        remaining = [point for point in remaining if point > chosen + 0.001]

                    if len(picked) != target_boundary_count:
                        step = len(boundaries) / (target_boundary_count + 1)
                        picked = [boundaries[int((i + 1) * step)]
                                  for i in range(target_boundary_count)]
                else:
                    picked = boundaries.copy()

                split_points = [0.0] + picked + [chunk_dur]
                windows: Dict[int, Tuple[float, float]] = {}
                for i, seg in enumerate(csegs):
                    start_offset = split_points[i]
                    duration = split_points[i + 1] - split_points[i]
                    windows[seg.index] = (max(0.0, start_offset), max(0.001, duration))
                return windows

            # Silence detection insufficient. Proportional slicing is the
            # classic source of speech running ahead of subtitles: if the TTS
            # model pauses differently from our character-count guess, words
            # from line N+1 can be extracted into line N. Default to a safe
            # per-segment fallback; allow opting in only for experiments.
            if target_boundary_count >= 1 and not allow_proportional_chunk_split:
                write_log(
                    ws,
                    f"  TTS chunk {getattr(chunk, 'chunk_index', '?')} has "
                    f"{len(boundaries)}/{target_boundary_count} usable silence boundaries; "
                    "falling back to per-segment synthesis for subtitle sync",
                )
                return None

            if target_boundary_count >= 1 and _max_segment_gap(csegs) > chunk_max_gap_sec:
                write_log(
                    ws,
                    f"  TTS chunk window fallback disabled: max internal gap "
                    f"{_max_segment_gap(csegs):.3f}s > {chunk_max_gap_sec:.3f}s",
                )
                return None

            units: List[Tuple[int, int, int]] = []
            cursor = 0
            for seg in csegs:
                seg_text = (seg.translation or seg.text or "").strip()
                seg_units = max(1, len(seg_text))
                units.append((seg.index, cursor, seg_units))
                cursor += seg_units + 1

            total_units = max(1, cursor - 1)
            windows_fallback: Dict[int, Tuple[float, float]] = {}
            for seg_index, start_unit, seg_units in units:
                start_offset = chunk_dur * (start_unit / total_units)
                duration = chunk_dur * (seg_units / total_units)
                windows_fallback[seg_index] = (start_offset, max(0.001, duration))
            return windows_fallback

        # ------------------------------------------------------------------
        # Per-segment synthesis (fast mode)
        # ------------------------------------------------------------------
        def synthesize_segment(seg: SubtitleSegment) -> Dict[str, Any]:
            if cancel_token.is_cancelled():
                return {"cancelled": True}

            seg_path = tts_dir / f"seg_{seg.index:04d}.wav"
            temp_path = tts_dir / f"seg_{seg.index:04d}_raw.wav"
            text = normalize_subtitle_text(seg.translation or seg.text)
            if not is_speech_subtitle_text(text):
                seg_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True)
                return {"skipped": True, "index": seg.index, "start": seg.start,
                        "target_duration": 0, "reason": "non-speech text"}
            target_dur_val = target_durations.get(seg.index, seg.end - seg.start)
            if target_dur_val <= 0:
                seg_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True)
                return {
                    "skipped": True, "index": seg.index, "start": seg.start,
                    "target_duration": target_dur_val,
                    "warning": "timing_warning: skipped, no safe gap before next segment",
                }
            return _synthesize_and_time(seg, text, temp_path, seg_path, target_dur_val)

        # ------------------------------------------------------------------
        # Chunk-based synthesis (stable / strict mode)
        # ------------------------------------------------------------------
        def synthesize_chunk(chunk: Any) -> List[Dict[str, Any]]:
            nonlocal chunk_fallback_count
            results: List[Dict[str, Any]] = []
            csegs = chunk_to_segments.get(chunk.chunk_index, [])
            if not csegs:
                return results

            chunk_path = tts_dir / f"chunk_{chunk.chunk_index:04d}.wav"
            chunk_temp = tts_dir / f"chunk_{chunk.chunk_index:04d}_raw.wav"

            try:
                result = provider.synthesize(
                    chunk.text.strip(), target_lang, tts_voice, chunk_temp, options,
                )
                if cancel_token.is_cancelled():
                    chunk_path.unlink(missing_ok=True)
                    chunk_temp.unlink(missing_ok=True)
                    return [{"cancelled": True}]
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                write_log(ws,
                    f"  TTS chunk {chunk.chunk_index} failed | "
                    f"segs={chunk.segment_indexes} "
                    f"text_preview={chunk.text.strip()[:100]} "
                    f"error={e}")
                write_log(ws, f"  TTS traceback:\n{tb}")
                error_type = "auth" if TTSAuthError and isinstance(e, TTSAuthError) else "generation"
                for seg in csegs:
                    seg_path = tts_dir / f"seg_{seg.index:04d}.wav"
                    seg_path.unlink(missing_ok=True)
                    results.append({
                        "error": str(e), "index": seg.index, "start": seg.start,
                        "target_duration": target_durations.get(seg.index, seg.end - seg.start),
                        "error_type": error_type,
                    })
                return results

            normalize_result = normalize_tts_audio(chunk_temp) if callable(normalize_tts_audio) else None
            normalized_chunk = Path(normalize_result) if normalize_result else chunk_temp
            if normalized_chunk != chunk_path:
                shutil.copy2(str(normalized_chunk), str(chunk_path))

            chunk_dur = _get_wav_duration_cached(chunk_path)
            if chunk_dur <= 0:
                chunk_dur = result.duration_seconds
            chunk_windows = _build_chunk_windows(csegs, chunk_dur, chunk_path)
            if chunk_windows is None:
                chunk_fallback_count += 1
                write_log(
                    ws,
                    f"  TTS chunk {chunk.chunk_index} crosses an unsafe timeline gap; "
                    "falling back to per-segment synthesis",
                )
                fallback_results: List[Dict[str, Any]] = []
                for seg in csegs:
                    result_item = synthesize_segment(seg)
                    if result_item.get("cancelled"):
                        fallback_results.append(result_item)
                        break
                    if result_item.get("index"):
                        warning = result_item.get("warning", "")
                        fallback_note = "chunk_fallback: unsafe chunk timeline"
                        result_item["warning"] = f"{warning}; {fallback_note}" if warning else fallback_note
                        result_item.update(_timeline_fields(
                            seg,
                            source_mode="chunk_fallback",
                            chunk_index=chunk.chunk_index,
                            chunk_segment_indexes=list(chunk.segment_indexes),
                        ))
                    fallback_results.append(result_item)
                return fallback_results

            single_segment_chunk = len(csegs) == 1

            for seg in csegs:
                seg_path = tts_dir / f"seg_{seg.index:04d}.wav"
                seg_target = target_durations.get(seg.index, seg.end - seg.start)

                if seg_target <= 0:
                    seg_path.unlink(missing_ok=True)
                    results.append({
                        "skipped": True, "index": seg.index, "start": seg.start,
                        "target_duration": seg_target,
                        "warning": "timing_warning: skipped, no safe gap before next segment",
                    })
                    continue

                start_offset, estimated_dur = chunk_windows.get(
                    seg.index, (0.0, seg_target)
                )

                if estimated_dur <= 0:
                    seg_path.unlink(missing_ok=True)
                    results.append({
                        "skipped": True, "index": seg.index, "start": seg.start,
                        "target_duration": seg_target,
                        "warning": "estimated duration zero",
                    })
                    continue

                if single_segment_chunk:
                    source_audio = chunk_path
                    source_duration = chunk_dur if chunk_dur > 0 else estimated_dur
                else:
                    source_audio = tts_dir / f"chunk_{chunk.chunk_index:04d}_seg_{seg.index:04d}.wav"
                    if not extract_audio_window(
                        chunk_path, source_audio, start_offset, estimated_dur,
                    ):
                        seg_path.unlink(missing_ok=True)
                        source_audio.unlink(missing_ok=True)
                        results.append({
                            "error": "failed to extract segment audio from chunk",
                            "index": seg.index,
                            "start": seg.start,
                            "target_duration": seg_target,
                            "actual_duration": estimated_dur,
                        })
                        continue
                    source_duration = estimated_dur

                if source_duration > 0:
                    adj_dur, warning, speed = _adjust_timing_for_window(
                        source_audio, seg_path, source_duration, seg_target,
                    )
                    if not seg_path.exists():
                        shutil.copy2(str(source_audio), str(seg_path))
                    # Warn prominently when hard-clipping removes a large
                    # portion of the speech in chunk mode too.
                    if source_duration > 0 and adj_dur > 0:
                        post_speed_dur = source_duration / (speed or 1.0)
                        clipped_sec = post_speed_dur - adj_dur
                        if clipped_sec > 0.25 and clipped_sec / post_speed_dur > 0.25:
                            write_log(
                                ws,
                                f"  TTS severe clipping | index={seg.index} "
                                f"chunk={chunk.chunk_index} "
                                f"source={source_duration:.2f}s speed={speed or 1.0:.2f}x "
                                f"post_speed={post_speed_dur:.2f}s "
                                f"target={seg_target:.2f}s "
                                f"clipped={clipped_sec:.2f}s ({clipped_sec / post_speed_dur * 100:.0f}%) — "
                                f"speech truncated, audio will not match subtitle",
                            )
                else:
                    shutil.copy2(str(source_audio), str(seg_path))
                    warning = ""
                    speed = None
                    adj_dur = estimated_dur

                payload = {
                    "index": seg.index,
                    "path": seg_path,
                    "start": seg.start,
                    "target_duration": seg_target,
                    "actual_duration": estimated_dur,
                    "adjusted_duration": adj_dur if seg_target > 0 else estimated_dur,
                    "speed": speed,
                    "warning": warning,
                    "cached": getattr(result, "cached", False),
                    "mode": getattr(result, "mode", options.get("qwen_mode", "")),
                }
                payload.update(_timeline_fields(
                    seg,
                    source_mode="chunk" if not single_segment_chunk else "single_chunk",
                    chunk_index=chunk.chunk_index,
                    chunk_segment_indexes=list(chunk.segment_indexes),
                    source_window_start=start_offset if not single_segment_chunk else 0.0,
                    source_window_duration=estimated_dur if not single_segment_chunk else source_duration,
                ))
                results.append(payload)

            return results

        # ------------------------------------------------------------------
        # Main synthesis loop
        # ------------------------------------------------------------------
        if tts_concurrency > 1:
            write_log(ws, f"  TTS concurrency: {tts_concurrency}")

        if use_chunking:
            for chunk in chunks:
                if cancel_token.is_cancelled():
                    self._mark_cancelled(job_id, ws)
                    return False
                chunk_results = synthesize_chunk(chunk)
                for result in chunk_results:
                    if result.get("cancelled"):
                        self._mark_cancelled(job_id, ws)
                        return False
                    record_report(result)
                    if result.get("error"):
                        tts_errors.append(result)
                        write_log(ws, f"  TTS failed for seg {result.get('index')}: {result['error']}")
                    elif result.get("skipped") and result.get("warning"):
                        write_log(ws, f"  TTS timing [{result.get('index')}]: {result['warning']}")
                    elif result.get("path"):
                        tts_segments.append((result["index"], result["path"], result["start"]))
                        if result.get("warning"):
                            write_log(ws, f"  TTS timing [{result['index']}]: {result['warning']}")
                        if result.get("speed") is not None:
                            speed_ratios[result["index"]] = result["speed"]
                completed += len(chunk_results)
                pct = int((completed / total) * 100) if total else 100
                self._progress.update(job_id, "tts", pct, f"TTS synthesis {completed}/{total}")
        elif tts_concurrency > 1:
            with ThreadPoolExecutor(max_workers=tts_concurrency) as executor:
                futures = [executor.submit(synthesize_segment, seg) for seg in candidates]
                for future in as_completed(futures):
                    if cancel_token.is_cancelled():
                        self._mark_cancelled(job_id, ws)
                        return False
                    result = future.result()
                    if result.get("cancelled"):
                        self._mark_cancelled(job_id, ws)
                        return False
                    record_report(result)
                    if result.get("error"):
                        tts_errors.append(result)
                        write_log(ws, f"  TTS failed for seg {result.get('index')}: {result['error']}")
                    elif result.get("skipped") and result.get("warning"):
                        write_log(ws, f"  TTS timing [{result.get('index')}]: {result['warning']}")
                    elif result.get("path"):
                        tts_segments.append((result["index"], result["path"], result["start"]))
                        if result.get("warning"):
                            write_log(ws, f"  TTS timing [{result.get('index')}]: {result['warning']}")
                        if result.get("speed") is not None:
                            speed_ratios[result["index"]] = result["speed"]
                    completed += 1
                    pct = int((completed / total) * 100) if total else 100
                    self._progress.update(
                        job_id, "tts", pct,
                        f"TTS synthesis {completed}/{total}",
                    )
        else:
            for seg in candidates:
                if cancel_token.is_cancelled():
                    self._mark_cancelled(job_id, ws)
                    return False
                result = synthesize_segment(seg)
                if result.get("cancelled"):
                    self._mark_cancelled(job_id, ws)
                    return False
                record_report(result)
                if result.get("error"):
                    tts_errors.append(result)
                    write_log(ws, f"  TTS failed for seg {result.get('index')}: {result['error']}")
                elif result.get("skipped") and result.get("warning"):
                    write_log(ws, f"  TTS timing [{result.get('index')}]: {result['warning']}")
                elif result.get("path"):
                    tts_segments.append((result["index"], result["path"], result["start"]))
                    if result.get("warning"):
                        write_log(ws, f"  TTS timing [{result['index']}]: {result['warning']}")
                    if result.get("speed") is not None:
                        speed_ratios[result["index"]] = result["speed"]
                completed += 1
                pct = int((completed / total) * 100) if total else 100
                self._progress.update(job_id, "tts", pct, f"TTS synthesis {completed}/{total}")

        if not tts_segments:
            if not candidates:
                self._fail(job_id, ws, "TTS_EMPTY_INPUT",
                           "没有可用于 TTS 的字幕文本，转写或翻译结果为空")
            else:
                audio_files = list(tts_dir.glob("seg_*.wav")) if tts_dir.exists() else []
                if tts_errors and not audio_files:
                    first_error = str(tts_errors[0].get("error") or "unknown TTS error")
                    if any(err.get("error_type") == "auth" for err in tts_errors):
                        self._fail(job_id, ws, "TTS_AUTH_FAILED",
                                   f"TTS 认证失败: {first_error}")
                    else:
                        self._fail(job_id, ws, "TTS_GENERATION_FAILED",
                                   f"TTS 语音合成失败: {first_error}")
                elif not audio_files:
                    self._fail(job_id, ws, "TTS_NO_AUDIO_OUTPUT",
                               "TTS 执行结束，但没有发现任何音频文件")
                else:
                    valid = [f for f in audio_files if f.stat().st_size > 0]
                    if not valid:
                        self._fail(job_id, ws, "TTS_ZERO_BYTE_AUDIO",
                                   "TTS 生成了音频文件，但所有文件大小均为 0")
                    else:
                        self._fail(job_id, ws, "TTS_GENERATION_FAILED",
                                   "TTS 语音合成执行异常")
            return False

        audio_files = list(tts_dir.glob("seg_*.wav")) if tts_dir.exists() else []
        zero_byte_files = [f for f in audio_files if f.stat().st_size == 0]
        if zero_byte_files:
            write_log(ws, f"  Warning: {len(zero_byte_files)} zero-byte TTS audio files detected")

        index_path = tts_dir / "index.json"
        tts_segments.sort(key=lambda item: item[2])
        index_data = [{"index": idx, "path": str(p), "start": s}
                      for idx, p, s in tts_segments]
        index_path.write_text(_json_module.dumps(index_data, ensure_ascii=False), encoding="utf-8")

        if speed_ratios:
            save_timing_report(speed_ratios, tts_dir / "timing_report.json")

        report_artifact = self._write_tts_control_report(
            ws, report_rows, tts_provider_name, tts_voice, options,
            consistency_mode=consistency_mode, profile_hash=profile_hash,
        )
        if report_artifact:
            self._store.add_artifact(job_id, report_artifact)

        timeline_artifact = self._write_tts_timeline_report(
            ws, report_rows,
            chunk_count=len(chunks),
            chunk_fallback_count=chunk_fallback_count,
            chunk_options=chunk_options,
            tts_plan=tts_plan.to_dict() if tts_plan is not None else None,
        )
        if timeline_artifact:
            self._store.add_artifact(job_id, timeline_artifact)

        self._store.update(job_id, stage="tts", message=f"TTS generated {completed} segments")
        return True

    def _ensure_qwen3_clone_prompt(self, provider: Any, options: Dict[str, Any], ws: Path) -> str:
        mode = str(options.get("qwen_mode", options.get("mode", "auto")) or "auto").lower()
        if mode != "voice_clone":
            return str(options.get("voice_clone_prompt_id", "") or "")
        if options.get("voice_clone_prompt_id"):
            return str(options.get("voice_clone_prompt_id"))
        ref_audio = str(options.get("ref_audio", "") or "").strip()
        if not ref_audio:
            return ""
        create_prompt = getattr(provider, "create_voice_clone_prompt", None)
        if not callable(create_prompt):
            return ""
        try:
            prompt_id = create_prompt(
                ref_audio,
                str(options.get("ref_text", "") or ""),
                bool(options.get("x_vector_only_mode", False)),
            )
            if prompt_id:
                write_log(ws, f"  Qwen3 voice clone prompt created: {prompt_id}")
            return prompt_id
        except Exception as exc:
            write_log(ws, f"  Qwen3 voice clone prompt creation failed: {exc}")
            return ""

    @staticmethod
    def _append_translation_punctuation(text: str, punctuation: str) -> str:
        base = PipelineRunner._clean_translation_text(text)
        punct = PipelineRunner._clean_translation_text(punctuation)
        if not punct:
            return base
        if not base:
            return punct
        if base.endswith(punct):
            return base
        return f"{base}{punct}"

    def _normalize_translation_segments(self, ws: Path,
                                        segments: List[SubtitleSegment],
                                        target_lang: str) -> None:
        """Clean translated text before export/TTS.

        Translation APIs sometimes return a standalone punctuation caption or
        preserve invisible/control characters.  Merge punctuation-only target
        captions into the previous translated sentence so exported subtitles and
        TTS inputs do not contain isolated "。"/"..." lines.
        """
        previous: Optional[SubtitleSegment] = None
        merged_punctuation = 0
        cleaned_count = 0

        for seg in sorted(segments, key=lambda item: (float(item.start), int(item.index))):
            cleaned = self._clean_translation_text(seg.translation)
            if cleaned != (seg.translation or ""):
                cleaned_count += 1
            seg.translation = cleaned

            if not cleaned:
                continue

            if punctuation_only(cleaned):
                if previous and previous.translation:
                    previous.translation = self._append_translation_punctuation(previous.translation, cleaned)
                    seg.translation = ""
                    seg.metadata["translation_skipped"] = "punctuation_only_merged"
                    merged_punctuation += 1
                continue

            previous = seg

        if cleaned_count or merged_punctuation:
            write_log(
                ws,
                f"  Translation normalized: cleaned={cleaned_count}, "
                f"merged_punctuation={merged_punctuation}, target={target_lang}",
            )

    @staticmethod
    def _has_blocking_translation_warnings(warnings: List[Tuple[str, str, Dict]]) -> bool:
        blocking_codes = {
            "EMPTY_TRANSLATION",
            "MISSING_TRANSLATION",
            "UNTRANSLATED",
            "TARGET_LANGUAGE_LEAK_JA",
            "TARGET_LANGUAGE_LEAK_JA_FRAGMENT",
            "TARGET_LANGUAGE_LEAK_KO",
            "TARGET_LANGUAGE_LEAK_NON_LATIN",
            "UNTRANSLATED_SOURCE_COPY",
            "SUSPICIOUS_TRANSLATION_ARTIFACT",
            "PUNCTUATION_ONLY_TRANSLATION",
        }
        return any(code in blocking_codes for code, _message, _context in warnings)

    def _write_translation_quality_report(self, ws: Path,
                                          segments: List[SubtitleSegment],
                                          target_lang: str = "",
                                          warnings: Optional[List[Tuple[str, str, Dict]]] = None) -> Optional[Dict]:
        if warnings is None:
            warnings = validate_translation(segments, target_lang)
        report_path = ws / "translation" / "translation_quality_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "target_language": target_lang,
            "total_segments": len(segments),
            "warning_count": len(warnings),
            "blocking_warning_count": sum(1 for item in warnings if self._has_blocking_translation_warnings([item])),
            "warnings": [
                {"code": code, "message": message, "context": context}
                for code, message, context in warnings
            ],
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "kind": "translation_quality_report",
            "path": "translation/translation_quality_report.json",
        }

    def _write_tts_control_report(self, ws: Path, rows: List[Dict[str, Any]],
                                  provider: str, voice: str,
                                  options: Dict[str, Any],
                                  consistency_mode: str = "",
                                  profile_hash: str = "") -> Optional[Dict]:
        report_path = ws / "audio" / "tts" / "tts_control_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        public_options = {
            key: value for key, value in options.items()
            if key not in {"voice_clone_prompt_id"}
        }
        payload = {
            "provider": provider,
            "voice": voice,
            "options": public_options,
            "consistency_mode": consistency_mode,
            "voice_profile_hash": profile_hash,
            "total_segments": len(rows),
            "cached_segments": sum(1 for row in rows if row.get("cached")),
            "skipped_segments": sum(1 for row in rows if row.get("skipped")),
            "error_segments": sum(1 for row in rows if row.get("error")),
            "segments": sorted(rows, key=lambda row: int(row.get("index") or 0)),
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "kind": "tts_control_report",
            "path": "audio/tts/tts_control_report.json",
        }

    def _write_tts_timeline_report(self, ws: Path, rows: List[Dict[str, Any]],
                                   *, chunk_count: int,
                                   chunk_fallback_count: int,
                                   chunk_options: Dict[str, Any],
                                   tts_plan: Optional[Dict[str, Any]] = None) -> Optional[Dict]:
        report_path = ws / "audio" / "tts" / "tts_timeline_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        warnings = [
            row for row in rows
            if row.get("warning")
            or row.get("error")
            or not bool(row.get("overlay_start_matches_subtitle_start", True))
        ]
        payload = {
            "total_segments": len(rows),
            "chunk_count": int(chunk_count),
            "chunk_fallback_count": int(chunk_fallback_count),
            "max_gap_sec": chunk_options.get("max_gap_sec"),
            "max_timeline_span_sec": chunk_options.get("max_timeline_span_sec"),
            "max_tolerance_sec": chunk_options.get("max_tolerance_sec"),
            "max_chunk_speed_factor": chunk_options.get("max_chunk_speed_factor"),
            "allow_proportional_chunk_split": chunk_options.get("allow_proportional_chunk_split"),
            "warnings_count": len(warnings),
            "tts_plan": tts_plan or {},
            "segments": sorted(rows, key=lambda row: int(row.get("index") or 0)),
        }
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "kind": "tts_timeline_report",
            "path": "audio/tts/tts_timeline_report.json",
        }

    def _run_audio_mix(self, job_id: str, ws: Path,
                        segments: List[SubtitleSegment],
                        source_video: Optional[Path],
                        target_lang: str,
                        request: Dict[str, Any],
                        cancel_token: CancellationToken) -> bool:
        """Mix TTS audio with original video audio."""
        if not source_video:
            self._fail(job_id, ws, "AUDIO_MIX_NO_VIDEO", "Source video is required for audio mix")
            return False

        tts_dir = ws / "audio" / "tts"
        ordered_segments = sorted(segments, key=lambda item: (float(item.start), int(item.index)))
        segment_start_by_index = {int(seg.index): float(seg.start) for seg in ordered_segments}
        segment_by_index = {int(seg.index): seg for seg in ordered_segments}
        next_start_by_index: Dict[int, Optional[float]] = {}
        for i, seg in enumerate(ordered_segments):
            next_start_by_index[int(seg.index)] = (
                float(ordered_segments[i + 1].start) if i + 1 < len(ordered_segments) else None
            )
        tts_segments: List[Tuple[int, Path, float]] = []
        index_path = tts_dir / "index.json"

        if index_path.exists():
            try:
                index_data = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception as exc:
                write_log(ws, f"  Failed to read TTS index.json, falling back to glob: {exc}")
                index_data = []
            for item in index_data if isinstance(index_data, list) else []:
                try:
                    idx = int(item.get("index"))
                except (TypeError, ValueError, AttributeError):
                    continue
                raw_path = str(item.get("path", "") or "")
                if not raw_path:
                    continue
                wav_path = Path(raw_path)
                if not wav_path.is_absolute():
                    wav_path = ws / wav_path
                if not wav_path.exists() or wav_path.stat().st_size <= 0:
                    write_log(ws, f"  TTS index entry skipped, missing audio: index={idx} path={wav_path}")
                    continue
                try:
                    indexed_start = float(item.get("start", segment_start_by_index.get(idx, 0.0)))
                except (TypeError, ValueError):
                    indexed_start = segment_start_by_index.get(idx, 0.0)
                current_start = segment_start_by_index.get(idx, indexed_start)
                if abs(indexed_start - current_start) > 0.05:
                    write_log(
                        ws,
                        f"  TTS index start mismatch for seg {idx}: "
                        f"index={indexed_start:.3f}s current={current_start:.3f}s; using current subtitle start",
                    )
                tts_segments.append((idx, wav_path, current_start))
        else:
            tts_wavs = sorted(tts_dir.glob("seg_*.wav"))
            for seg in segments:
                match = [w for w in tts_wavs if w.stem.endswith(f"{seg.index:04d}")]
                if match and match[0].stat().st_size > 0:
                    tts_segments.append((int(seg.index), match[0], float(seg.start)))

        if not tts_segments:
            self._fail(job_id, ws, "AUDIO_MIX_NO_TTS", "No TTS audio files found")
            return False

        tts_segments.sort(key=lambda item: item[2])

        from audio.mix import get_audio_duration, mix_audio
        from tts.timing import extract_audio_window

        tts_options = dict(request.get("tts_options", {}) or {})
        try:
            mix_gap = float(tts_options.get("tts_segment_gap", request.get("tts_segment_gap", 0.04)) or 0.04)
        except (TypeError, ValueError):
            mix_gap = 0.04
        mix_gap = max(0.0, min(0.25, mix_gap))

        def _prepare_mix_safe_segments() -> Tuple[List[Tuple[Path, float]], Optional[Dict[str, Any]]]:
            """Final guardrail before FFmpeg amix.

            Even if an older cached seg_*.wav or a provider quirk ignores timing,
            this pass prevents one clip from bleeding into the next subtitle
            window. It writes a small report so misalignment can be diagnosed
            without listening through the whole render.
            """
            safe_dir = tts_dir / "mix_safe"
            safe_dir.mkdir(parents=True, exist_ok=True)
            safe_items: List[Tuple[Path, float]] = []
            rows: List[Dict[str, Any]] = []
            clipped_count = 0

            for idx, wav_path, start in tts_segments:
                if cancel_token.is_cancelled():
                    break
                duration = get_audio_duration(wav_path)
                next_start = next_start_by_index.get(int(idx))
                max_duration = None
                clipped = False
                output_wav = wav_path

                if next_start is not None:
                    max_duration = max(0.02, float(next_start) - float(start) - mix_gap)
                    if duration > 0 and duration > max_duration + 0.03:
                        clipped_wav = safe_dir / f"seg_{int(idx):04d}_mixsafe.wav"
                        if extract_audio_window(wav_path, clipped_wav, 0.0, max_duration):
                            output_wav = clipped_wav
                            duration = get_audio_duration(clipped_wav) or max_duration
                            clipped = True
                            clipped_count += 1
                        else:
                            write_log(
                                ws,
                                f"  TTS mix-safe trim failed for seg {idx}; using original audio",
                            )

                safe_items.append((output_wav, float(start)))
                seg = segment_by_index.get(int(idx))
                rows.append({
                    "index": int(idx),
                    "path": str(output_wav),
                    "source_path": str(wav_path),
                    "start": float(start),
                    "duration": duration,
                    "subtitle_start": float(seg.start) if seg is not None else None,
                    "subtitle_end": float(seg.end) if seg is not None else None,
                    "next_subtitle_start": next_start,
                    "max_duration_before_next": max_duration,
                    "gap_sec": mix_gap,
                    "clipped_for_mix": clipped,
                })

            report = {
                "total_segments": len(rows),
                "clipped_segments": clipped_count,
                "gap_sec": mix_gap,
                "segments": rows,
            } if rows else None
            if report:
                report_path = tts_dir / "mix_alignment_report.json"
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self._store.add_artifact(job_id, {
                    "kind": "tts_mix_alignment_report",
                    "path": "audio/tts/mix_alignment_report.json",
                })
                if clipped_count:
                    write_log(ws, f"  TTS mix-safe clipped {clipped_count} segment(s) to prevent overlap")
            return safe_items, report

        def cc():
            return cancel_token.is_cancelled()

        output_path = ws / "rendered" / f"{ws.name}_{target_lang}_dubbed.mp4"
        try:
            original_volume = float(request.get("original_volume", 0.0))
        except (TypeError, ValueError):
            original_volume = 0.0
        original_volume = max(0.0, min(1.0, original_volume))

        mix_message = (
            "Mixing TTS audio with original audio..."
            if original_volume > 0
            else "Replacing original audio with TTS audio..."
        )
        self._progress.update(job_id, "audio_mix", 10, mix_message)
        mix_inputs, _mix_report = _prepare_mix_safe_segments()
        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return False
        if not mix_inputs:
            self._fail(job_id, ws, "AUDIO_MIX_NO_TTS", "No usable TTS audio files found after alignment checks")
            return False
        result = mix_audio(
            video_path=source_video,
            tts_segments=mix_inputs,
            output_path=output_path,
            original_volume=original_volume,
            cancel_checker=cc,
            log_path=ws / "logs" / "ffmpeg.log",
        )

        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return False

        if not result.get("success"):
            self._fail(job_id, ws, "AUDIO_MIX_FAILED",
                       result.get("error", "Audio mix failed"))
            return False

        self._store.add_artifact(job_id, {
            "kind": "dubbed_video",
            "path": f"rendered/{output_path.name}",
            "language": target_lang,
        })
        self._progress.update(job_id, "audio_mix", 100, "Audio mix complete")
        return True

    @staticmethod
    def _default_tts_voice(language: str) -> str:
        voices = {
            "zh": "zh-CN-XiaoxiaoNeural",
            "zh-CN": "zh-CN-XiaoxiaoNeural",
            "en": "en-US-JennyNeural",
            "ja": "ja-JP-NanamiNeural",
            "ko": "ko-KR-SunHiNeural",
            "fr": "fr-FR-DeniseNeural",
            "de": "de-DE-KatjaNeural",
            "es": "es-ES-ElviraNeural",
        }
        return voices.get(language, "zh-CN-XiaoxiaoNeural")

    def _mark_cancelled(self, job_id: str, ws: Path) -> None:
        self._store.update(
            job_id, status="cancelled", stage="cancelled",
            message="Task cancelled", error_code="TASK_CANCELLED",
        )
        self._progress.update(job_id, "cancelled", 0, "Task cancelled")
        write_log(ws, "Pipeline cancelled")

    def _fail(self, job_id: str, ws: Path, code: str, message: str) -> None:
        self._store.update(
            job_id, status="error", stage="error",
            message=message, error_code=code, error_detail=message,
        )
        self._progress.update(job_id, "error", 0, message)
        write_log(ws, f"Pipeline failed: [{code}] {message}")


def start_pipeline(
    job_id: str,
    request: Dict[str, Any],
    task_store: TaskStore,
    progress: ProgressTracker,
    cancel_token: CancellationToken,
) -> threading.Thread:
    runner = PipelineRunner(task_store, progress)
    thread = threading.Thread(
        target=runner.run_job,
        args=(job_id, request, cancel_token),
        daemon=True,
        name=f"pipeline-{job_id[:8]}",
    )
    thread.start()
    return thread
