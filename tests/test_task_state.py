import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.task_state import TaskStatus, TaskInfo, status_to_ui_text, normalize_status


class TestTaskState(unittest.TestCase):
    def test_task_status_values(self):
        self.assertEqual(TaskStatus.PENDING.value, "pending")
        self.assertEqual(TaskStatus.QUEUED.value, "queued")
        self.assertEqual(TaskStatus.DOWNLOADING.value, "downloading")
        self.assertEqual(TaskStatus.PROCESSING.value, "processing")
        self.assertEqual(TaskStatus.SAVING.value, "saving")
        self.assertEqual(TaskStatus.COMPLETED.value, "completed")
        self.assertEqual(TaskStatus.ERROR.value, "error")
        self.assertEqual(TaskStatus.CANCELLED.value, "cancelled")

    def test_status_to_ui_text(self):
        self.assertEqual(status_to_ui_text(TaskStatus.PENDING), "等待处理")
        self.assertEqual(status_to_ui_text(TaskStatus.QUEUED), "排队中")
        self.assertEqual(status_to_ui_text(TaskStatus.DOWNLOADING), "下载中")
        self.assertEqual(status_to_ui_text(TaskStatus.PROCESSING), "处理中")
        self.assertEqual(status_to_ui_text(TaskStatus.SAVING), "保存中")
        self.assertEqual(status_to_ui_text(TaskStatus.COMPLETED), "已完成")
        self.assertEqual(status_to_ui_text(TaskStatus.ERROR), "失败")
        self.assertEqual(status_to_ui_text(TaskStatus.CANCELLED), "已取消")

    def test_normalize_status_from_string(self):
        self.assertIs(normalize_status("pending"), TaskStatus.PENDING)
        self.assertIs(normalize_status("queued"), TaskStatus.QUEUED)
        self.assertIs(normalize_status("downloading"), TaskStatus.DOWNLOADING)
        self.assertIs(normalize_status("processing"), TaskStatus.PROCESSING)
        self.assertIs(normalize_status("saving"), TaskStatus.SAVING)
        self.assertIs(normalize_status("completed"), TaskStatus.COMPLETED)
        self.assertIs(normalize_status("error"), TaskStatus.ERROR)
        self.assertIs(normalize_status("cancelled"), TaskStatus.CANCELLED)

    def test_normalize_status_from_enum(self):
        self.assertIs(normalize_status(TaskStatus.PENDING), TaskStatus.PENDING)
        self.assertIs(normalize_status(TaskStatus.CANCELLED), TaskStatus.CANCELLED)

    def test_normalize_status_unknown(self):
        self.assertIs(normalize_status("unknown"), TaskStatus.PENDING)
        self.assertIs(normalize_status(""), TaskStatus.PENDING)

    def test_normalize_status_none_ish(self):
        self.assertIs(normalize_status(None), TaskStatus.PENDING)

    def test_task_info_defaults(self):
        info = TaskInfo(key="test.mp4")
        self.assertEqual(info.key, "test.mp4")
        self.assertIs(info.status, TaskStatus.PENDING)
        self.assertEqual(info.progress, 0)
        self.assertEqual(info.message, "")
        self.assertEqual(info.error, "")
        self.assertEqual(info.language, "auto")
        self.assertFalse(info.is_url)

    def test_task_info_status_transition(self):
        info = TaskInfo(key="test.mp4")
        info.status = TaskStatus.COMPLETED
        self.assertIs(info.status, TaskStatus.COMPLETED)
        info.status = TaskStatus.CANCELLED
        self.assertIs(info.status, TaskStatus.CANCELLED)

    def test_task_info_with_url(self):
        info = TaskInfo(key="https://youtube.com/watch?v=test", is_url=True)
        self.assertTrue(info.is_url)
        self.assertIs(info.status, TaskStatus.PENDING)

    def test_task_str_roundtrip(self):
        for status in TaskStatus:
            self.assertEqual(str(status), status.value)
            self.assertIs(normalize_status(status.value), status)


if __name__ == "__main__":
    unittest.main()
