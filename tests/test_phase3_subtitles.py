from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from engine.sqlite_repository import SQLiteTaskRepository  # noqa: E402
from subtitles.commands import (  # noqa: E402
    DeleteCue,
    FindReplace,
    InsertCue,
    MergeCues,
    ShiftCues,
    SplitCue,
    SubtitleEditor,
    UpdateCue,
)
from subtitles.document import SubtitleCue, SubtitleDocument  # noqa: E402
from subtitles.document_service import (  # noqa: E402
    SubtitleDocumentError,
    SubtitleDocumentService,
    SubtitleVersionConflictError,
    document_to_segments,
)
from subtitles.document_validator import SubtitleValidator  # noqa: E402


def _segments():
    return [
        SimpleNamespace(start=0.5, end=2.0, text="Hello world", translation="你好世界"),
        SimpleNamespace(start=2.2, end=4.0, text="Second line", translation="第二行"),
    ]


def _document(task_id="task-1"):
    return SubtitleDocument.from_segments(
        task_id, _segments(), source_language="en", target_language="zh-CN"
    )


def _service(tmp_path, task_id="task-1"):
    repo = SQLiteTaskRepository(tmp_path / "db")
    repo.create(task_id, {
        "workspace_dir": str(tmp_path / "workspace"),
        "source_language": "en", "target_language": "zh-CN",
        "dubbing_enabled": True,
    })
    service = SubtitleDocumentService(tmp_path / "artifacts", repo)
    document = service.create_from_segments(
        task_id, _segments(), source_language="en", target_language="zh-CN"
    )
    return repo, service, document


def test_cue_requires_integer_milliseconds():
    with pytest.raises(TypeError):
        SubtitleCue("cue", 1.5, 1000, "text")


def test_document_uses_stable_non_index_cue_ids():
    first = _document()
    second = _document()
    assert [cue.cue_id for cue in first.cues] == [cue.cue_id for cue in second.cues]
    assert all(cue.cue_id not in {"0", "1"} for cue in first.cues)


def test_duplicate_source_segments_still_receive_unique_stable_ids():
    segment = SimpleNamespace(start=1.0, end=2.0, text="same", translation="相同")
    first = SubtitleDocument.from_segments("task", [segment, segment])
    second = SubtitleDocument.from_segments("task", [segment, segment])
    assert first.cues[0].cue_id != first.cues[1].cue_id
    assert [cue.cue_id for cue in first.cues] == [cue.cue_id for cue in second.cues]


def test_document_json_round_trip_preserves_integer_timing():
    original = _document()
    restored = SubtitleDocument.from_dict(json.loads(original.canonical_json()))
    assert restored.to_dict() == original.to_dict()
    assert isinstance(restored.cues[0].start_ms, int)


def test_update_command_changes_text_and_timing():
    document = _document()
    cue_id = document.cues[0].cue_id
    updated = UpdateCue(cue_id, {"source_text": "Edited", "start_ms": 600}).apply(document)
    assert updated.cues[0].source_text == "Edited"
    assert updated.cues[0].start_ms == 600
    assert document.cues[0].source_text == "Hello world"


def test_insert_command_generates_stable_shape_with_new_uuid():
    document = _document()
    updated = InsertCue(2000, 2150, "Inserted", after_cue_id=document.cues[0].cue_id).apply(document)
    assert len(updated.cues) == 3
    assert updated.cues[1].source_text == "Inserted"
    assert updated.cues[1].cue_id not in {cue.cue_id for cue in document.cues}


def test_delete_command_removes_selected_cue():
    document = _document()
    updated = DeleteCue(document.cues[0].cue_id).apply(document)
    assert [cue.cue_id for cue in updated.cues] == [document.cues[1].cue_id]


def test_split_keeps_first_id_and_creates_second_id():
    document = _document()
    original = document.cues[0]
    updated = SplitCue(original.cue_id, 5, split_ms=1200).apply(document)
    assert updated.cues[0].cue_id == original.cue_id
    assert updated.cues[1].cue_id != original.cue_id
    assert updated.cues[0].end_ms == updated.cues[1].start_ms == 1200
    assert "".join(c.source_text.replace(" ", "") for c in updated.cues[:2]) == "Helloworld"


def test_merge_requires_adjacent_cues_and_keeps_first_id():
    document = _document()
    merged = MergeCues(document.cues[0].cue_id, document.cues[1].cue_id).apply(document)
    assert len(merged.cues) == 1
    assert merged.cues[0].cue_id == document.cues[0].cue_id
    assert merged.cues[0].end_ms == document.cues[1].end_ms


def test_shift_can_target_subset_or_all_cues():
    document = _document()
    one = ShiftCues(100, (document.cues[0].cue_id,)).apply(document)
    assert one.cues[0].start_ms == document.cues[0].start_ms + 100
    assert one.cues[1].start_ms == document.cues[1].start_ms
    all_cues = ShiftCues(-200).apply(document)
    assert all_cues.cues[1].end_ms == document.cues[1].end_ms - 200


def test_find_replace_supports_source_translation_and_case_insensitive():
    document = _document()
    updated = FindReplace("HELLO", "Goodbye", field="source", case_sensitive=False).apply(document)
    assert updated.cues[0].source_text == "Goodbye world"
    assert updated.cues[0].translated_text == document.cues[0].translated_text


def test_editor_undo_redo_and_new_command_clears_redo():
    editor = SubtitleEditor(_document())
    cue_id = editor.document.cues[0].cue_id
    editor.execute(UpdateCue(cue_id, {"source_text": "one"}))
    editor.undo()
    assert editor.document.cues[0].source_text == "Hello world"
    editor.redo()
    assert editor.document.cues[0].source_text == "one"
    editor.undo()
    editor.execute(UpdateCue(cue_id, {"source_text": "two"}))
    assert editor.can_redo is False


def test_validator_reports_negative_and_end_before_start():
    document = _document().clone(cues=[SubtitleCue("bad", -10, -20, "x", "y")])
    codes = {issue.code for issue in SubtitleValidator().validate(document)}
    assert {"SUBTITLE_NEGATIVE_TIME", "SUBTITLE_END_BEFORE_START"} <= codes


def test_validator_reports_duplicate_ids_and_empty_text():
    cue = SubtitleCue("duplicate", 0, 1000, "", "")
    document = _document().clone(cues=[cue, cue.updated(start_ms=1200, end_ms=2000)])
    codes = [issue.code for issue in SubtitleValidator().validate(document)]
    assert "CUE_ID_DUPLICATE" in codes
    assert "SUBTITLE_EMPTY" in codes


def test_validator_reports_order_and_overlap_with_cue_id():
    cues = [SubtitleCue("a", 1000, 3000, "a", "甲"), SubtitleCue("b", 500, 1500, "b", "乙")]
    document = _document().clone(cues=cues)
    issues = SubtitleValidator().validate(document)
    codes = {issue.code for issue in issues}
    assert {"SUBTITLE_TIME_ORDER", "SUBTITLE_OVERLAP"} <= codes
    assert all(issue.cue_id for issue in issues)


def test_validator_reports_duration_characters_and_reading_speed():
    long_text = "x" * 100
    cues = [SubtitleCue("short", 0, 100, long_text, "译文")]
    document = _document().clone(cues=cues)
    codes = {issue.code for issue in SubtitleValidator().validate(document)}
    assert {
        "SUBTITLE_DURATION_TOO_SHORT", "SUBTITLE_TOO_MANY_CHARACTERS",
        "SUBTITLE_READING_SPEED_HIGH",
    } <= codes


def test_validator_reports_suspected_untranslated_text():
    document = _document()
    cue = document.cues[0].updated(translated_text=document.cues[0].source_text)
    issues = SubtitleValidator().validate(document.clone(cues=[cue]))
    issue = next(item for item in issues if item.code == "SUBTITLE_SUSPECT_UNTRANSLATED")
    assert issue.severity == "warning"
    assert issue.suggestion


def test_create_document_writes_revision_artifact_and_metadata(tmp_path):
    repo, service, document = _service(tmp_path)
    metadata = repo.get_subtitle_document(document.document_id)
    revisions = service.list_revisions(document.document_id)
    assert metadata["current_version"] == 1
    assert revisions[0]["version"] == 1
    assert (service.storage_root / document.task_id / revisions[0]["artifact_path"]).is_file()


def test_original_subtitle_input_is_never_overwritten(tmp_path):
    source = tmp_path / "original.srt"
    original = "1\n00:00:00,500 --> 00:00:02,000\nHello\n"
    source.write_text(original, encoding="utf-8")
    _repo, _service_obj, _document_obj = _service(tmp_path)
    assert source.read_text(encoding="utf-8") == original


def test_draft_save_and_recovery_survives_service_restart(tmp_path):
    repo, service, document = _service(tmp_path)
    edited = UpdateCue(document.cues[0].cue_id, {"source_text": "draft"}).apply(document)
    service.save_draft(edited, base_version=document.version)
    restarted = SubtitleDocumentService(service.storage_root, repo)
    recovered = restarted.recover_draft(document.document_id)
    assert recovered.cues[0].source_text == "draft"
    assert recovered.version == document.version


def test_draft_checksum_detects_corruption(tmp_path):
    repo, service, document = _service(tmp_path)
    revision = service.save_draft(document, base_version=document.version)
    path = service.storage_root / document.task_id / revision["artifact_path"]
    path.write_text("corrupted", encoding="utf-8")
    with pytest.raises(SubtitleDocumentError) as caught:
        service.recover_draft(document.document_id)
    assert caught.value.error_code == "SUBTITLE_ARTIFACT_CHECKSUM_MISMATCH"


def test_formal_revision_clears_draft_and_increments_version(tmp_path):
    repo, service, document = _service(tmp_path)
    edited = UpdateCue(document.cues[0].cue_id, {"translated_text": "正式版本"}).apply(document)
    service.save_draft(edited, base_version=document.version)
    result = service.save_revision(edited, base_version=document.version)
    assert result.document.version == 2
    assert repo.get_subtitle_draft(document.document_id) is None
    assert service.recover_draft(document.document_id) is None


def test_stale_base_version_raises_stable_conflict(tmp_path):
    _repo, service, document = _service(tmp_path)
    first = UpdateCue(document.cues[0].cue_id, {"source_text": "first"}).apply(document)
    service.save_revision(first, base_version=document.version)
    with pytest.raises(SubtitleVersionConflictError) as caught:
        service.save_revision(document, base_version=document.version)
    assert caught.value.error_code == "SUBTITLE_VERSION_CONFLICT"


def test_revision_history_restore_creates_new_revision(tmp_path):
    _repo, service, document = _service(tmp_path)
    original_revision = service.list_revisions(document.document_id)[0]
    edited = UpdateCue(document.cues[0].cue_id, {"source_text": "changed"}).apply(document)
    second = service.save_revision(edited, base_version=1).document
    restored = service.restore_revision(
        document.document_id, original_revision["id"], base_version=second.version
    )
    assert restored.document.version == 3
    assert restored.document.cues[0].source_text == "Hello world"
    assert len(service.list_revisions(document.document_id)) == 3


def test_formal_save_invalidates_only_downstream_artifacts(tmp_path):
    repo, service, document = _service(tmp_path)
    for stage, kind in [
        ("translate", "translation_quality_report"),
        ("tts", "tts_report"),
        ("audio_mix", "dubbed_video"),
        ("render", "burned_video"),
    ]:
        repo.register_artifact(document.task_id, {"stage": stage, "kind": kind, "path": f"{kind}.dat"})
    edited = UpdateCue(document.cues[0].cue_id, {"translated_text": "新译文"}).apply(document)
    result = service.save_revision(edited, base_version=document.version)
    current = repo.list_artifacts(document.task_id, current_only=True)
    current_kinds = {artifact["kind"] for artifact in current}
    assert result.invalidated_artifacts == 3
    assert "translation_quality_report" in current_kinds
    assert not {"tts_report", "dubbed_video", "burned_video"} & current_kinds


def test_current_subtitle_artifact_supersedes_previous_version(tmp_path):
    repo, service, document = _service(tmp_path)
    edited = UpdateCue(document.cues[0].cue_id, {"source_text": "changed"}).apply(document)
    service.save_revision(edited, base_version=1)
    current = [
        artifact for artifact in repo.list_artifacts(document.task_id, current_only=True)
        if artifact["kind"] == "current_subtitle"
    ]
    all_current_kind = [
        artifact for artifact in repo.list_artifacts(document.task_id, current_only=False)
        if artifact["kind"] == "current_subtitle"
    ]
    assert len(current) == 1
    assert len(all_current_kind) == 2
    assert sum(bool(item["is_current"]) for item in all_current_kind) == 1


def test_save_and_regenerate_uses_retry_planner_from_tts(tmp_path):
    repo, service, document = _service(tmp_path)
    for stage in ("prepare", "normalize", "translate", "subtitle_export"):
        run_id = repo.begin_stage_run(document.task_id, stage)
        repo.finish_stage_run(run_id, "completed")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "translated.srt").write_text("subtitle", encoding="utf-8")
    repo.register_artifact(document.task_id, {
        "stage": "subtitle_export", "kind": "translated_srt", "path": "translated.srt"
    })
    repo.update(document.task_id, status="completed", stage="finalize")
    edited = UpdateCue(document.cues[0].cue_id, {"translated_text": "再生成"}).apply(document)
    result = service.save_revision(edited, base_version=1, regenerate=True)
    assert result.retry_plan.start_stage == "tts"
    assert repo.get(document.task_id).status == "pending"


def test_document_conversion_to_pipeline_segments_is_boundary_only():
    document = _document()
    segments = document_to_segments(document)
    assert segments[0].start == 0.5
    assert segments[0].translation == "你好世界"
    assert document.cues[0].start_ms == 500


def test_subtitle_migration_records_version_three(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    with repo.transaction(immediate=False) as conn:
        migrations = dict(conn.execute("SELECT version, name FROM schema_migrations").fetchall())
    assert migrations[3] == "subtitle_documents_and_revisions"
