"""Tests for project_workspace module."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from project_workspace import (
    ProjectWorkspace,
    atomic_write_json,
    clean_stage_artifacts,
    create_project_workspace,
    ensure_workspace_dirs,
    file_fingerprint,
    safe_dirname,
    safe_filename,
    validate_path_within,
    workspace_path,
)


class TestSafeFilename(unittest.TestCase):
    def test_normal_ascii(self):
        self.assertEqual(safe_filename("hello world"), "hello world")

    def test_chinese_characters(self):
        result = safe_filename("中文视频标题")
        self.assertEqual(result, "中文视频标题")

    def test_windows_illegal_chars(self):
        result = safe_filename('video<>:"|?*name')
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertNotIn(":", result)
        self.assertNotIn('"', result)
        self.assertNotIn("|", result)
        self.assertNotIn("?", result)
        self.assertNotIn("*", result)

    def test_windows_reserved_name(self):
        result = safe_filename("CON")
        self.assertFalse(result.upper() == "CON")

    def test_windows_reserved_name_with_ext(self):
        result = safe_filename("NUL.txt")
        self.assertFalse(result.startswith("NUL"))

    def test_empty_string_fallback(self):
        result = safe_filename("")
        self.assertEqual(result, "video")

    def test_none_fallback(self):
        result = safe_filename(None)
        self.assertEqual(result, "video")

    def test_dots_only_fallback(self):
        result = safe_filename("...")
        self.assertEqual(result, "video")

    def test_long_name_truncation(self):
        long_name = "a" * 200
        result = safe_filename(long_name)
        self.assertLessEqual(len(result), 120)

    def test_mixed_content(self):
        result = safe_filename("My Video: 测试 [HD]")
        self.assertIn("My Video", result)
        self.assertIn("测试", result)

    def test_spaces_preserved(self):
        result = safe_filename("hello world")
        self.assertIn(" ", result)


class TestSafeDirname(unittest.TestCase):
    def test_format(self):
        result = safe_dirname("test title", "12345678-1234-1234-1234-123456789012")
        self.assertEqual(result, "test title__12345678")

    def test_chinese_title(self):
        result = safe_dirname("中文标题", "abcdef12-3456-7890-abcd-ef1234567890")
        self.assertIn("中文标题", result)
        self.assertIn("__abcdef12", result)

    def test_short_job_id(self):
        result = safe_dirname("test", "ab")
        self.assertIn("__ab", result)


class TestValidatePathWithin(unittest.TestCase):
    def test_valid_path(self):
        base = Path("/tmp/workspace")
        target = Path("/tmp/workspace/subtitles/source.srt")
        self.assertTrue(validate_path_within(base, target))

    def test_path_traversal(self):
        base = Path("/tmp/workspace")
        target = Path("/tmp/workspace/../../../etc/passwd")
        self.assertFalse(validate_path_within(base, target))

    def test_sibling_path(self):
        base = Path("/tmp/workspace")
        target = Path("/tmp/other/file.txt")
        self.assertFalse(validate_path_within(base, target))

    def test_same_path(self):
        base = Path("/tmp/workspace")
        self.assertTrue(validate_path_within(base, base))


class TestWorkspacePath(unittest.TestCase):
    def test_valid_path(self):
        result = workspace_path(Path("/tmp/ws"), "subtitles", "source.srt")
        self.assertTrue(str(result).endswith("source.srt"))

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            workspace_path(Path("/tmp/ws"), "..", "..", "etc", "passwd")


class TestEnsureWorkspaceDirs(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_all_subdirs(self):
        ws = Path(self.tmpdir) / "test_workspace"
        ensure_workspace_dirs(ws)
        self.assertTrue((ws / "source").is_dir())
        self.assertTrue((ws / "subtitles").is_dir())
        self.assertTrue((ws / "translation").is_dir())
        self.assertTrue((ws / "audio" / "tts").is_dir())
        self.assertTrue((ws / "rendered").is_dir())
        self.assertTrue((ws / "logs").is_dir())

    def test_idempotent(self):
        ws = Path(self.tmpdir) / "test_workspace"
        ensure_workspace_dirs(ws)
        ensure_workspace_dirs(ws)  # Should not raise
        self.assertTrue(ws.is_dir())


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_and_read(self):
        path = Path(self.tmpdir) / "test.json"
        data = {"key": "value", "number": 42, "chinese": "中文"}
        atomic_write_json(path, data)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["key"], "value")
        self.assertEqual(loaded["chinese"], "中文")

    def test_overwrite_existing(self):
        path = Path(self.tmpdir) / "test.json"
        atomic_write_json(path, {"v": 1})
        atomic_write_json(path, {"v": 2})
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["v"], 2)

    def test_no_temp_files_left(self):
        path = Path(self.tmpdir) / "test.json"
        atomic_write_json(path, {"key": "value"})
        files = list(Path(self.tmpdir).iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "test.json")

    def test_creates_parent_dirs(self):
        path = Path(self.tmpdir) / "deep" / "nested" / "test.json"
        atomic_write_json(path, {"ok": True})
        self.assertTrue(path.exists())


class TestFileFingerprint(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_consistent_hash(self):
        path = Path(self.tmpdir) / "test.txt"
        path.write_text("hello world", encoding="utf-8")
        h1 = file_fingerprint(path)
        h2 = file_fingerprint(path)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_different_content(self):
        p1 = Path(self.tmpdir) / "a.txt"
        p2 = Path(self.tmpdir) / "b.txt"
        p1.write_text("hello", encoding="utf-8")
        p2.write_text("world", encoding="utf-8")
        self.assertNotEqual(file_fingerprint(p1), file_fingerprint(p2))

    def test_nonexistent_file(self):
        h = file_fingerprint(Path(self.tmpdir) / "nope.txt")
        self.assertEqual(h, "")


class TestCleanStageArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        ws = Path(self.tmpdir)
        # Create some fake artifacts
        (ws / "subtitles").mkdir(exist_ok=True)
        (ws / "rendered").mkdir(exist_ok=True)
        (ws / "audio" / "tts").mkdir(parents=True, exist_ok=True)
        (ws / "subtitles" / "source.srt").write_text("srt", encoding="utf-8")
        (ws / "subtitles" / "zh-CN.srt").write_text("srt", encoding="utf-8")
        (ws / "rendered" / "output.mp4").write_text("mp4", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_clean_render(self):
        ws = Path(self.tmpdir)
        clean_stage_artifacts(ws, "render")
        self.assertFalse((ws / "rendered" / "output.mp4").exists())
        # Subtitles should still exist
        self.assertTrue((ws / "subtitles" / "source.srt").exists())

    def test_clean_translate(self):
        ws = Path(self.tmpdir)
        clean_stage_artifacts(ws, "translate")
        self.assertFalse((ws / "subtitles" / "source.srt").exists())
        # Rendered should still exist
        self.assertTrue((ws / "rendered" / "output.mp4").exists())

    def test_clean_unknown_stage(self):
        ws = Path(self.tmpdir)
        # Should not delete anything
        clean_stage_artifacts(ws, "unknown_stage")
        self.assertTrue((ws / "subtitles" / "source.srt").exists())


class TestCreateProjectWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_creation(self):
        ws = create_project_workspace(Path(self.tmpdir), "Test Video")
        self.assertTrue(ws.exists())
        self.assertTrue((ws / "source").is_dir())
        self.assertIn("Test Video", ws.name)

    def test_chinese_title(self):
        ws = create_project_workspace(Path(self.tmpdir), "中文测试视频")
        self.assertTrue(ws.exists())
        self.assertIn("中文测试视频", ws.name)

    def test_with_job_id(self):
        job_id = "12345678-abcd-ef01-2345-678901234567"
        ws = create_project_workspace(Path(self.tmpdir), "Test", job_id)
        self.assertIn("12345678", ws.name)

    def test_duplicate_titles_get_unique_dirs(self):
        ws1 = create_project_workspace(Path(self.tmpdir), "Same Title", "aaaa1111-0000")
        ws2 = create_project_workspace(Path(self.tmpdir), "Same Title", "bbbb2222-0000")
        self.assertNotEqual(ws1, ws2)

    def test_workspace_metadata(self):
        ws = create_project_workspace(Path(self.tmpdir), "My Video", "abcd1234")
        meta_path = ws / ".workspace.json"
        self.assertTrue(meta_path.exists())
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["job_id"], "abcd1234")
        self.assertEqual(meta["title"], "My Video")


class TestProjectWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ws_dir = Path(self.tmpdir) / "test__12345678"
        ensure_workspace_dirs(self.ws_dir)
        self.ws = ProjectWorkspace(self.ws_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_subdir_properties(self):
        self.assertEqual(self.ws.source_dir, self.ws_dir / "source")
        self.assertEqual(self.ws.subtitles_dir, self.ws_dir / "subtitles")
        self.assertEqual(self.ws.tts_dir, self.ws_dir / "audio" / "tts")
        self.assertEqual(self.ws.rendered_dir, self.ws_dir / "rendered")

    def test_path_validation(self):
        p = self.ws.path("subtitles", "test.srt")
        self.assertTrue(str(p).endswith("test.srt"))

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.ws.path("..", "..", "etc", "passwd")

    def test_ensure(self):
        new_ws = ProjectWorkspace(Path(self.tmpdir) / "new_workspace")
        new_ws.ensure()
        self.assertTrue(new_ws.dir.is_dir())
        self.assertTrue((new_ws.dir / "source").is_dir())

    def test_clean_stage(self):
        (self.ws.subtitles_dir / "test.srt").write_text("srt", encoding="utf-8")
        self.ws.clean_stage("subtitle_export")
        self.assertFalse((self.ws.subtitles_dir / "test.srt").exists())


if __name__ == "__main__":
    unittest.main()
