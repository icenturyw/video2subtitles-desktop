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
        SubtitleStyle, TaskResult, TranslationConfig,
    )
    from subtitle_ass import segments_to_ass, save_ass
    from services.ffmpeg_service import find_ffmpeg, render_hardsub, render_softsub
    from subtitles.normalize import read_subtitle_file
    from subtitles.srt_writer import write_srt, write_vtt, write_txt
    from subtitles.validate import validate_timeline, validate_translation
    from translation.batching import (
        CheckpointManager, batch_segments, batch_to_request,
    )
    from translation.glossary import Glossary as Gloss
    from translation.openai_compatible import get_provider
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

        warnings = validate_timeline(segments)
        for w in warnings:
            write_log(ws, f"  Timeline warning [{w[0]}]: {w[1]}")

        self._progress.update(job_id, "normalize", 100, f"Loaded {len(segments)} subtitle segments")

        if low_vram_mode and dubbing and self._is_qwen3_tts_request(request):
            self._unload_qwen3_tts(ws, "before translation")

        # --- TRANSLATE ---
        if resume_stage == "translate" or resume_stage in ("prepare", "normalize"):
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

        if target_lang and source_lang.lower() != target_lang.lower():
            report = self._write_translation_quality_report(ws, segments)
            if report:
                self._store.add_artifact(job_id, report)

        # --- SUBTITLE_EXPORT ---
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
        if dubbing:
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
        if dubbing:
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

        if dubbing:
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
            return prepared
        try:
            seed_int = int(seed)
        except (TypeError, ValueError):
            prepared["seed"] = _QWEN3_DEFAULT_STABLE_SEED
            prepared["seed_policy"] = "default_stable"
            return prepared
        if seed_int < 0:
            prepared.pop("seed", None)
            prepared["seed_policy"] = "random"
        else:
            prepared["seed"] = seed_int
            prepared["seed_policy"] = "explicit"
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
                                  batch: List[SubtitleSegment]) -> List[Dict]:
            try:
                translations = request_translation(batch)
            except Exception as exc:
                if len(batch) <= 1:
                    seg = batch[0]
                    write_log(
                        ws,
                        f"  Batch {batch_index}: segment {seg.index} translation failed; using source text ({exc})",
                    )
                    return [{"id": seg.index, "text": seg.text}]
                mid = max(1, len(batch) // 2)
                write_log(
                    ws,
                    f"  Batch {batch_index}: translation response invalid; splitting {len(batch)} segments",
                )
                return (
                    complete_translations(batch_index, batch[:mid])
                    + complete_translations(batch_index, batch[mid:])
                )

            valid_by_id = {
                int(t["id"]): str(t.get("text", ""))
                for t in translations
                if isinstance(t, dict)
                and str(t.get("id", "")).isdigit()
                and int(t.get("id")) in {s.index for s in batch}
                and str(t.get("text", "")).strip()
            }
            missing = [seg for seg in batch if seg.index not in valid_by_id]
            if missing:
                if len(batch) <= 1:
                    seg = batch[0]
                    write_log(
                        ws,
                        f"  Batch {batch_index}: segment {seg.index} missing/empty translation; using source text",
                    )
                    valid_by_id[seg.index] = seg.text
                else:
                    write_log(
                        ws,
                        f"  Batch {batch_index}: retrying {len(missing)} missing/empty translations",
                    )
                    for item in complete_translations(batch_index, missing):
                        if str(item.get("text", "")).strip():
                            valid_by_id[int(item["id"])] = str(item["text"])

            return [{"id": seg.index, "text": valid_by_id.get(seg.index, seg.text)} for seg in batch]

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

            self._apply_translation_result(batch, translations)
            checkpoints.mark_completed([s.index for s in batch])
            write_log(ws, f"  Batch {batch_index}/{total} translated ({len(batch)} segments)")
            completed_batches += 1
            self._progress.update(
                job_id, "translate", int((completed_batches / total) * 100),
                f"Translated batch {completed_batches}/{total}",
            )

        return True

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

        try:
            from tts import get_provider as get_tts_provider
            provider = get_tts_provider(tts_provider_name, cache_dir=tts_dir)
        except (ImportError, ValueError) as e:
            self._fail(job_id, ws, "TTS_UNAVAILABLE",
                       f"TTS provider {tts_provider_name} unavailable: {e}")
            return False

        from tts.timing import adjust_timing, save_timing_report

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

        tts_segments: List[Tuple[int, Path, float]] = []
        speed_ratios = {}
        candidates = [
            seg for seg in segments
            if (seg.translation or seg.text or "").strip()
        ]
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

        target_durations = {}
        for i, seg in enumerate(candidates):
            nominal = max(min_tts_duration, seg.end - seg.start)
            if i + 1 < len(candidates):
                next_start = candidates[i + 1].start
                available = next_start - seg.start - tts_gap
                target_durations[seg.index] = (
                    min(nominal, available)
                    if available >= min_tts_duration
                    else 0.0
                )
            else:
                target_durations[seg.index] = nominal

        write_log(ws, f"  TTS engine: {tts_provider_name}, voice: {tts_voice}, language: {target_lang}")
        write_log(ws, f"  TTS segments: {total}, concurrency: {tts_concurrency}")
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

        def synthesize_segment(seg: SubtitleSegment) -> Dict[str, Any]:
            if cancel_token.is_cancelled():
                return {"cancelled": True}

            seg_path = tts_dir / f"seg_{seg.index:04d}.wav"
            temp_path = tts_dir / f"seg_{seg.index:04d}_raw.wav"
            text = seg.translation or seg.text
            if not text.strip():
                seg_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True)
                return {"skipped": True, "index": seg.index, "start": seg.start,
                        "target_duration": target_dur, "reason": "empty text"}
            target_dur = target_durations.get(seg.index, seg.end - seg.start)
            if target_dur <= 0:
                seg_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True)
                return {
                    "skipped": True,
                    "index": seg.index,
                    "start": seg.start,
                    "target_duration": target_dur,
                    "warning": "timing_warning: skipped, no safe gap before next segment",
                }

            try:
                result = provider.synthesize(
                    text.strip(), target_lang, tts_voice, temp_path, options,
                )
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
                return {"error": str(e), "index": seg.index, "start": seg.start,
                        "target_duration": target_dur}

            actual_dur = result.duration_seconds
            speed = None
            warning = ""

            if actual_dur > 0 and target_dur > 0:
                adj_dur, warning, speed = adjust_timing(
                    temp_path, seg_path, actual_dur, target_dur,
                )
                if not seg_path.exists():
                    shutil.copy2(str(temp_path), str(seg_path))
            else:
                shutil.copy2(str(temp_path), str(seg_path))

            return {
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

        def record_report(result: Dict[str, Any]) -> None:
            if not result.get("index"):
                return
            report_rows.append({
                "index": result.get("index"),
                "start": result.get("start"),
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

        if tts_concurrency > 1:
            write_log(ws, f"  TTS concurrency: {tts_concurrency}")
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
                if not audio_files:
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

        import json
        index_path = tts_dir / "index.json"
        tts_segments.sort(key=lambda item: item[2])
        index_data = [{"index": idx, "path": str(p), "start": s}
                      for idx, p, s in tts_segments]
        index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")

        if speed_ratios:
            save_timing_report(speed_ratios, tts_dir / "timing_report.json")

        report_artifact = self._write_tts_control_report(
            ws, report_rows, tts_provider_name, tts_voice, options
        )
        if report_artifact:
            self._store.add_artifact(job_id, report_artifact)

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

    def _write_translation_quality_report(self, ws: Path,
                                          segments: List[SubtitleSegment]) -> Optional[Dict]:
        warnings = validate_translation(segments)
        report_path = ws / "translation" / "translation_quality_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "total_segments": len(segments),
            "warning_count": len(warnings),
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
                                  options: Dict[str, Any]) -> Optional[Dict]:
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
        tts_wavs = sorted(tts_dir.glob("seg_*.wav"))
        if not tts_wavs:
            self._fail(job_id, ws, "AUDIO_MIX_NO_TTS", "No TTS audio files found")
            return False

        tts_segments = []
        for seg in segments:
            match = [w for w in tts_wavs if w.stem.endswith(f"{seg.index:04d}")]
            if match:
                tts_segments.append((match[0], seg.start))

        from audio.mix import mix_audio

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
        result = mix_audio(
            video_path=source_video,
            tts_segments=tts_segments,
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
