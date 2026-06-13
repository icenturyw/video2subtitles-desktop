"""Tests for manifest v2 support in output_manifest and history."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from output_manifest import (
    get_manifest_version,
    load_manifest,
    load_manifest_v2,
    update_chatgpt_package,
    update_manifest_artifacts,
    update_manifest_checkpoints,
    write_manifest,
    write_manifest_v2,
)
from history import HistoryManager


class TestManifestVersion(unittest.TestCase):
    def test_v1_detection(self):
        self.assertEqual(get_manifest_version({"title": "test"}), 1)
        self.assertEqual(get_manifest_version({}), 1)

    def test_v2_detection(self):
        self.assertEqual(get_manifest_version({"schema_version": 2}), 2)


class TestWriteManifestV1(unittest.TestCase):
    """Ensure existing v1 write_manifest still works unchanged."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_write(self):
        result = write_manifest(
            Path(self.tmpdir),
            source="https://youtube.com/watch?v=test",
            title="Test Video",
            language="en",
            is_url=True,
        )
        self.assertEqual(result["title"], "Test Video")
        self.assertEqual(result["source"], "https://youtube.com/watch?v=test")
        self.assertTrue(result["is_url"])
        # v1 should NOT have schema_version
        self.assertNotIn("schema_version", result)

    def test_v1_roundtrip(self):
        write_manifest(
            Path(self.tmpdir),
            source="test.mp4",
            title="Roundtrip",
            language="zh",
        )
        loaded = load_manifest(Path(self.tmpdir))
        self.assertEqual(loaded["title"], "Roundtrip")
        self.assertEqual(loaded["language"], "zh")


class TestWriteManifestV2(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_v2_write(self):
        result = write_manifest_v2(
            Path(self.tmpdir),
            job_id="test-uuid-123",
            title="V2 Video",
            source="test.mp4",
            mode="translate",
            target_language="zh-CN",
            subtitle_mode="bilingual",
            burn_subtitles=True,
        )
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["job_id"], "test-uuid-123")
        self.assertEqual(result["title"], "V2 Video")
        self.assertEqual(result["pipeline"]["mode"], "translate")
        self.assertEqual(result["pipeline"]["target_language"], "zh-CN")
        self.assertTrue(result["pipeline"]["burn_subtitles"])
        # v1 compat fields preserved
        self.assertEqual(result["source"], "test.mp4")

    def test_v2_with_artifacts(self):
        artifacts = [
            {"kind": "source_srt", "path": "subtitles/source.srt", "language": "en"},
            {"kind": "translated_srt", "path": "subtitles/zh-CN.srt", "language": "zh-CN"},
        ]
        result = write_manifest_v2(
            Path(self.tmpdir),
            job_id="art-test",
            title="Artifacts",
            artifacts=artifacts,
        )
        self.assertEqual(len(result["artifacts"]), 2)

    def test_v2_with_checkpoints(self):
        result = write_manifest_v2(
            Path(self.tmpdir),
            job_id="cp-test",
            checkpoints={"transcribe": True, "translate": False},
        )
        self.assertTrue(result["checkpoints"]["transcribe"])
        self.assertFalse(result["checkpoints"]["translate"])

    def test_v2_preserves_existing(self):
        # Write initial
        write_manifest_v2(Path(self.tmpdir), job_id="first", title="First")
        # Write again with updated info
        result = write_manifest_v2(Path(self.tmpdir), job_id="first", title="Updated")
        self.assertEqual(result["title"], "Updated")
        # created_at should be from first write
        loaded = load_manifest(Path(self.tmpdir))
        self.assertIn("created_at", loaded)


class TestLoadManifestV2(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_v1_as_v2(self):
        # Write v1 manifest
        write_manifest(Path(self.tmpdir), source="old.mp4", title="Old V1", language="en")
        # Load as v2
        v2 = load_manifest_v2(Path(self.tmpdir))
        self.assertEqual(v2["schema_version"], 2)
        self.assertEqual(v2["title"], "Old V1")
        self.assertEqual(v2["pipeline"]["detected_language"], "en")
        self.assertEqual(v2["pipeline"]["mode"], "subtitle")
        # v1 fields preserved in merged dict
        self.assertEqual(v2["source"], "old.mp4")

    def test_load_v2_directly(self):
        write_manifest_v2(
            Path(self.tmpdir),
            job_id="v2-direct",
            title="V2 Direct",
            mode="translate",
            target_language="ja",
        )
        v2 = load_manifest_v2(Path(self.tmpdir))
        self.assertEqual(v2["schema_version"], 2)
        self.assertEqual(v2["pipeline"]["target_language"], "ja")

    def test_load_empty_dir(self):
        v2 = load_manifest_v2(Path(self.tmpdir))
        self.assertEqual(v2, {})

    def test_v1_upgrade_has_artifacts(self):
        write_manifest(
            Path(self.tmpdir),
            source="test.mp4",
            title="V1 Artifacts",
            srt_path=Path(self.tmpdir) / "test.srt",
            video_path=Path(self.tmpdir) / "test.mp4",
        )
        v2 = load_manifest_v2(Path(self.tmpdir))
        artifacts = v2.get("artifacts", [])
        kinds = [a["kind"] for a in artifacts]
        self.assertIn("source_video", kinds)
        self.assertIn("source_srt", kinds)

    def test_v1_does_not_modify_file(self):
        write_manifest(Path(self.tmpdir), source="test.mp4", title="Immutable")
        # Load as v2
        load_manifest_v2(Path(self.tmpdir))
        # Original file should still be v1
        raw = load_manifest(Path(self.tmpdir))
        self.assertEqual(get_manifest_version(raw), 1)


class TestUpdateManifestArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_update_artifacts(self):
        write_manifest_v2(Path(self.tmpdir), job_id="upd-art")
        new_arts = [{"kind": "burned_video", "path": "rendered/out.mp4"}]
        result = update_manifest_artifacts(Path(self.tmpdir), new_arts)
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertEqual(result["artifacts"][0]["kind"], "burned_video")


class TestUpdateManifestCheckpoints(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_update_checkpoints(self):
        write_manifest_v2(
            Path(self.tmpdir),
            job_id="upd-cp",
            checkpoints={"transcribe": True},
        )
        result = update_manifest_checkpoints(
            Path(self.tmpdir), {"translate": True, "render": True}
        )
        self.assertTrue(result["checkpoints"]["transcribe"])
        self.assertTrue(result["checkpoints"]["translate"])
        self.assertTrue(result["checkpoints"]["render"])


class TestChatgptPackageCompat(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_update_on_v2_manifest(self):
        write_manifest_v2(Path(self.tmpdir), job_id="cg-test")
        pkg_dir = Path(self.tmpdir) / "chatgpt_package"
        pkg_dir.mkdir()
        result = update_chatgpt_package(Path(self.tmpdir), pkg_dir)
        self.assertIn("chatgpt_package_dir", result)
        # v2 fields should still be present
        self.assertEqual(result["schema_version"], 2)


class TestHistoryV2Compat(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_path = Path(self.tmpdir) / "history.json"
        self.history = HistoryManager(self.history_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_v1_entry_still_works(self):
        entry = self.history.make_entry(
            subtitles=[{"text": "hello"}],
            language="en",
            srt_path="/tmp/test.srt",
            output_dir="/tmp/output",
            title="V1 Entry",
        )
        self.history.put("v1-key", entry)
        loaded = self.history.get("v1-key")
        self.assertEqual(loaded["title"], "V1 Entry")
        self.assertEqual(loaded["language"], "en")

    def test_v2_entry(self):
        entry = self.history.make_entry_v2(
            job_id="uuid-123",
            title="V2 Entry",
            output_dir="/tmp/output",
            source="test.mp4",
            mode="translate",
            target_language="zh-CN",
            language="en",
            subtitle_count=50,
        )
        self.history.put("v2-key", entry)
        loaded = self.history.get("v2-key")
        self.assertEqual(loaded["schema_version"], 2)
        self.assertEqual(loaded["job_id"], "uuid-123")
        self.assertEqual(loaded["mode"], "translate")

    def test_get_entry_mode_v1(self):
        entry = self.history.make_entry([], "en", "", "/tmp")
        self.history.put("k1", entry)
        self.assertEqual(self.history.get_entry_mode("k1"), "subtitle")

    def test_get_entry_mode_v2(self):
        entry = self.history.make_entry_v2(
            job_id="j1", title="T", output_dir="/tmp", mode="dub"
        )
        self.history.put("k2", entry)
        self.assertEqual(self.history.get_entry_mode("k2"), "dub")

    def test_get_job_id_v1(self):
        entry = self.history.make_entry([], "en", "", "/tmp")
        self.history.put("k1", entry)
        self.assertEqual(self.history.get_job_id("k1"), "")

    def test_get_job_id_v2(self):
        entry = self.history.make_entry_v2(
            job_id="abc-def", title="T", output_dir="/tmp"
        )
        self.history.put("k3", entry)
        self.assertEqual(self.history.get_job_id("k3"), "abc-def")

    def test_mixed_entries(self):
        v1 = self.history.make_entry([{"text": "a"}], "en", "", "/tmp/v1", title="Old")
        v2 = self.history.make_entry_v2(
            job_id="new-1", title="New", output_dir="/tmp/v2", mode="translate"
        )
        self.history.put("old-key", v1)
        self.history.put("new-key", v2)
        all_entries = self.history.all_entries()
        self.assertEqual(len(all_entries), 2)
        self.assertEqual(all_entries["old-key"]["title"], "Old")
        self.assertEqual(all_entries["new-key"]["mode"], "translate")


if __name__ == "__main__":
    unittest.main()
