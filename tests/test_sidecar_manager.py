from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sidecar_manager import (
    SidecarManager,
    _find_pid_on_port,
    _find_python,
    _is_port_open,
    _kill_pid,
)


class TestIsPortOpen(unittest.TestCase):
    @patch("socket.socket")
    def test_open_port_returns_true(self, mock_socket):
        instance = mock_socket.return_value
        instance.connect_ex.return_value = 0
        self.assertTrue(_is_port_open("127.0.0.1", 8766))

    @patch("socket.socket")
    def test_closed_port_returns_false(self, mock_socket):
        instance = mock_socket.return_value
        instance.connect_ex.return_value = 1
        self.assertFalse(_is_port_open("127.0.0.1", 8766))


class TestFindPidOnPort(unittest.TestCase):
    @patch("services.sidecar_manager.subprocess.run")
    def test_finds_pid(self, mock_run):
        mock_run.return_value.stdout = (
            "  TCP    0.0.0.0:8766    0.0.0.0:0    LISTENING    12345\n"
        )
        pid = _find_pid_on_port(8766)
        self.assertEqual(pid, "12345")

    @patch("services.sidecar_manager.subprocess.run")
    def test_no_listening_line_returns_none(self, mock_run):
        mock_run.return_value.stdout = (
            "  TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    9999\n"
        )
        pid = _find_pid_on_port(8766)
        self.assertIsNone(pid)

    @patch("services.sidecar_manager.subprocess.run", side_effect=Exception("err"))
    def test_exception_returns_none(self, mock_run):
        pid = _find_pid_on_port(8766)
        self.assertIsNone(pid)


class TestKillPid(unittest.TestCase):
    @patch("services.sidecar_manager.subprocess.run")
    def test_kills_on_windows(self, mock_run):
        with patch("services.sidecar_manager.os.name", "nt"):
            _kill_pid("12345")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("taskkill", args)

    @patch("services.sidecar_manager.os.kill")
    @patch("services.sidecar_manager.subprocess.run")
    def test_kills_on_unix(self, mock_run, mock_kill):
        with patch("services.sidecar_manager.os.name", "posix"):
            _kill_pid("12345")
            mock_kill.assert_called_once_with(12345, 15)  # SIGTERM

    @patch("services.sidecar_manager.subprocess.run", side_effect=Exception("err"))
    def test_exception_does_not_raise(self, mock_run):
        with patch("services.sidecar_manager.os.name", "nt"):
            _kill_pid("12345")


class TestFindPython(unittest.TestCase):
    def test_finds_venv_python(self):
        with tempfile.TemporaryDirectory() as td:
            venv = Path(td) / "venv"
            scripts = venv / "Scripts"
            scripts.mkdir(parents=True)
            python_exe = scripts / "python.exe"
            python_exe.write_text("")
            result = _find_python([Path(td)], fallback="python")
            self.assertEqual(result, str(python_exe))

    def test_falls_back_when_no_venv(self):
        with tempfile.TemporaryDirectory() as td:
            result = _find_python([Path(td)], fallback="/usr/bin/python3")
            self.assertEqual(result, "/usr/bin/python3")


class TestSidecarManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.service_dir = self.tmp / "service"
        self.service_dir.mkdir(parents=True)
        (self.service_dir / "main.py").write_text("print('ok')")
        self.manager = SidecarManager(
            name="TestService",
            port=9876,
            service_dir=self.service_dir,
            log_dir=self.tmp / "logs",
            startup_timeout=1.0,
        )

    def test_ensure_running_when_already_healthy(self):
        with patch.object(self.manager, "_is_healthy", return_value=True):
            with patch.object(self.manager, "_kill_stale") as mock_kill:
                result = self.manager.ensure_running()
                self.assertTrue(result)
                mock_kill.assert_not_called()

    def test_ensure_running_when_missing_dir(self):
        manager = SidecarManager("Missing", 9876, self.tmp / "nonexistent")
        result = manager.ensure_running()
        self.assertFalse(result)

    def test_ensure_running_when_missing_script(self):
        empty_dir = self.tmp / "empty"
        empty_dir.mkdir()
        manager = SidecarManager("NoScript", 9876, empty_dir)
        result = manager.ensure_running()
        self.assertFalse(result)

    @patch.object(SidecarManager, "_is_healthy", side_effect=[False, True])
    @patch.object(SidecarManager, "_kill_stale")
    @patch("services.sidecar_manager.subprocess.Popen")
    def test_ensure_running_starts_successfully(self, mock_popen, mock_kill, mock_health):
        result = self.manager.ensure_running()
        self.assertTrue(result)
        mock_popen.assert_called_once()

    @patch.object(SidecarManager, "_is_healthy", return_value=False)
    @patch.object(SidecarManager, "_kill_stale")
    @patch("services.sidecar_manager.subprocess.Popen")
    def test_ensure_running_timeout(self, mock_popen, mock_kill, mock_health):
        result = self.manager.ensure_running()
        self.assertFalse(result)

    @patch.object(SidecarManager, "_is_healthy", side_effect=[False, True])
    @patch.object(SidecarManager, "_kill_stale")
    @patch("services.sidecar_manager.subprocess.Popen")
    def test_restart_starts_fresh(self, mock_popen, mock_kill, mock_health):
        result = self.manager.restart()
        self.assertTrue(result)

    def test_shutdown_terminates_process(self):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        self.manager._process = mock_process
        with patch.object(self.manager, "_kill_stale"):
            self.manager.shutdown()
            mock_process.terminate.assert_called_once()

    def test_shutdown_does_nothing_if_no_process(self):
        self.manager._process = None
        self.manager.shutdown()

    def test_is_healthy_calls_health_endpoint(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.status = 200
            self.assertTrue(self.manager.is_healthy())

    def test_is_healthy_returns_false_on_exception(self):
        with patch("urllib.request.urlopen", side_effect=Exception("err")):
            self.assertFalse(self.manager.is_healthy())

    def test_get_server_info_returns_json(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            resp = MagicMock()
            resp.read.return_value = b'{"name": "test", "version": "1.0"}'
            mock_urlopen.return_value.__enter__.return_value = resp
            info = self.manager.get_server_info()
            self.assertEqual(info["name"], "test")
            self.assertEqual(info["version"], "1.0")

    def test_get_server_info_returns_none_on_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("err")):
            self.assertIsNone(self.manager.get_server_info())

    def test_status_callback_called(self):
        calls = []
        manager = SidecarManager(
            name="Svc", port=1234, service_dir=self.service_dir,
            status_callback=lambda s, d: calls.append((s, d)),
        )
        manager._status("testing", "hello")
        self.assertIn(("testing", "hello"), calls)

    @patch.object(SidecarManager, "_is_healthy", return_value=True)
    @patch.object(SidecarManager, "_kill_stale")
    def test_start_logs_to_file(self, mock_kill, mock_health):
        log_dir = self.tmp / "logs2"
        manager = SidecarManager(
            name="LogTest", port=9877, service_dir=self.service_dir,
            log_dir=log_dir,
        )
        with patch("services.sidecar_manager.subprocess.Popen"):
            manager._start(self.service_dir / "main.py")
            log_file = log_dir / "service.log"
            self.assertTrue(log_file.exists())
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("启动", content)
