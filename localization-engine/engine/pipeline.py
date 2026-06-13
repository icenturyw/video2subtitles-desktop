"""Pipeline orchestrator for the localization engine.

Full pipeline stages:
    prepare -> normalize -> translate -> subtitle_export -> render -> finalize

Each stage checks cancellation and progress, updates the task store,
and supports retry from any stage via checkpoints.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    from subtitles.validate import validate_timeline
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
                       "无法读取源字幕文件")
            return

        warnings = validate_timeline(segments)
        for w in warnings:
            write_log(ws, f"  Timeline warning [{w[0]}]: {w[1]}")

        self._progress.update(job_id, "normalize", 100, f"读取了 {len(segments)} 条字幕")

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
        dubbing = request.get("dubbing_enabled", False)
        if dubbing:
            if self._check_cancel(job_id, ws, cancel_token):
                return
            self._store.update(job_id, stage="tts")
            self._progress.update(job_id, "tts", 0, "语音合成...")
            write_log(ws, "Stage: tts")

            tts_success = self._run_tts(job_id, ws, segments, request, target_lang, cancel_token)
            if not tts_success:
                return

            self._progress.update(job_id, "tts", 100, "语音合成完成")

        # --- AUDIO_MIX (dub mode only) ---
        if dubbing:
            if self._check_cancel(job_id, ws, cancel_token):
                return
            self._store.update(job_id, stage="audio_mix")
            self._progress.update(job_id, "audio_mix", 0, "音频混合...")
            write_log(ws, "Stage: audio_mix")

            mix_success = self._run_audio_mix(job_id, ws, segments, source_video, target_lang, cancel_token)
            if not mix_success:
                return

            self._progress.update(job_id, "audio_mix", 100, "音频混合完成")

        # --- RENDER ---
        if self._check_cancel(job_id, ws, cancel_token):
            return
        self._store.update(job_id, stage="render")

        if burn and source_video:
            self._progress.update(job_id, "render", 0, "准备渲染...")
            write_log(ws, "Stage: render")

            sub_path = self._find_ass_for_render(ws, subtitle_mode, target_lang)
            if sub_path and sub_path.exists():
                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    self._fail(job_id, ws, "FFMPEG_NOT_FOUND", "未找到 FFmpeg")
                    return

                log_path = ws / "logs" / "ffmpeg.log"
                output_name = f"{ws.name}_{target_lang or 'sub'}.mp4"
                output_path = ws / "rendered" / output_name

                result = render_hardsub(
                    video_path=source_video,
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
                        video_path=source_video,
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

        batches = batch_segments(pending, max_chars=trans_config.max_batch_chars)

        provider_name = trans_config.provider
        try:
            provider = get_provider(provider_name)
        except ValueError:
            self._fail(job_id, ws, "TRANSLATION_ERROR",
                       f"Unknown provider: {provider_name}")
            return False

        total = len(batches)
        completed_batches = 0

        for batch in batches:
            if cancel_token.is_cancelled():
                self._mark_cancelled(job_id, ws)
                return False

            batch_pct = int((completed_batches / total) * 100)
            self._progress.update(
                job_id, "translate", batch_pct,
                f"翻译批次 {completed_batches + 1}/{total}",
            )

            batch_req = batch_to_request(batch)
            try:
                translations = provider.translate_batch(
                    batch_req, trans_config, source_lang, target_lang,
                    glossary=glossary_text,
                )
            except Exception as e:
                self._fail(job_id, ws, "TRANSLATION_ERROR", str(e)[:200])
                return False

            # Apply translations
            trans_map = {t["id"]: t["text"] for t in translations}
            for seg in batch:
                if seg.index in trans_map:
                    seg.translation = trans_map[seg.index]

            # Mark checkpoint
            checkpoints.mark_completed([s.index for s in batch])
            write_log(ws, f"  Batch {completed_batches + 1}/{total} translated ({len(batch)} segments)")
            completed_batches += 1

        return True

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
        if not tts_voice:
            tts_voice = self._default_tts_voice(target_lang)

        try:
            from tts.edge_tts import _synthesize_sync
        except ImportError:
            self._fail(job_id, ws, "TTS_UNAVAILABLE", "TTS 模块不可用")
            return False

        from tts.timing import adjust_timing

        tts_segments = []
        total = len([s for s in segments if s.translation])
        completed = 0

        for seg in segments:
            if cancel_token.is_cancelled():
                self._mark_cancelled(job_id, ws)
                return False

            text = seg.translation or seg.text
            if not text.strip():
                continue

            seg_path = tts_dir / f"seg_{seg.index:04d}.wav"
            temp_path = tts_dir / f"seg_{seg.index:04d}_raw.wav"

            try:
                result = _synthesize_sync(text.strip(), tts_voice, temp_path)
            except Exception as e:
                write_log(ws, f"  TTS failed for seg {seg.index}: {e}")
                continue

            actual_dur = result.duration_seconds
            target_dur = seg.end - seg.start

            if actual_dur > 0 and target_dur > 0:
                adj_dur, warning = adjust_timing(temp_path, seg_path, actual_dur, target_dur)
                if warning:
                    write_log(ws, f"  TTS timing [{seg.index}]: {warning}")
                if not seg_path.exists():
                    import shutil
                    shutil.copy2(str(temp_path), str(seg_path))
            else:
                import shutil
                shutil.copy2(str(temp_path), str(seg_path))

            tts_segments.append((seg_path, seg.start))
            completed += 1
            pct = int((completed / total) * 100) if total else 100
            self._progress.update(job_id, "tts", pct, f"语音合成 {completed}/{total}")

        if not tts_segments:
            self._fail(job_id, ws, "TTS_NO_OUTPUT", "未生成任何语音")
            return False

        # Save TTS segment index for audio_mix stage
        import json
        index_path = tts_dir / "index.json"
        index_data = [{"index": i, "path": str(p), "start": s}
                      for i, (p, s) in enumerate(tts_segments)]
        index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")

        self._store.update(job_id, stage="tts", message=f"语音合成 {completed} 句")
        return True

    def _run_audio_mix(self, job_id: str, ws: Path,
                        segments: List[SubtitleSegment],
                        source_video: Optional[Path],
                        target_lang: str,
                        cancel_token: CancellationToken) -> bool:
        """Mix TTS audio with original video audio."""
        if not source_video:
            self._fail(job_id, ws, "AUDIO_MIX_NO_VIDEO", "找不到源视频文件")
            return False

        tts_dir = ws / "audio" / "tts"
        index_path = tts_dir / "index.json"
        if not index_path.exists():
            self._fail(job_id, ws, "AUDIO_MIX_NO_TTS", "未找到 TTS 语音数据")
            return False

        import json
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        tts_segments = [(Path(p), s) for item in index_data for p, s in [item]]

        from audio.mix import mix_audio

        def cc():
            return cancel_token.is_cancelled()

        output_path = ws / "rendered" / f"{ws.name}_{target_lang}_dubbed.mp4"

        self._progress.update(job_id, "audio_mix", 10, "正在混合音频...")
        result = mix_audio(
            video_path=source_video,
            tts_segments=tts_segments,
            output_path=output_path,
            original_volume=0.3,
            cancel_checker=cc,
            log_path=ws / "logs" / "ffmpeg.log",
        )

        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return False

        if not result.get("success"):
            self._fail(job_id, ws, "AUDIO_MIX_FAILED",
                       result.get("error", "音频混合失败"))
            return False

        self._store.add_artifact(job_id, {
            "kind": "dubbed_video",
            "path": f"rendered/{output_path.name}",
            "language": target_lang,
        })
        self._progress.update(job_id, "audio_mix", 100, "音频混合完成")
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
            message="任务已取消", error_code="TASK_CANCELLED",
        )
        self._progress.update(job_id, "cancelled", 0, "任务已取消")
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
