import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


class _DummyQApplication:
    @staticmethod
    def setAttribute(*args, **kwargs):
        return None

    def __init__(self, *args, **kwargs):
        self.aboutToQuit = types.SimpleNamespace(connect=lambda callback: None)

    def setApplicationName(self, *args, **kwargs):
        return None

    def setApplicationDisplayName(self, *args, **kwargs):
        return None

    def setFont(self, *args, **kwargs):
        return None

    def exec_(self):
        return 0


class _DummyQFont:
    PreferAntialias = 1

    def __init__(self, *args, **kwargs):
        pass

    def setStyleStrategy(self, *args, **kwargs):
        return None


def _install_app_import_stubs(root: Path):
    modules = {}

    client_settings = types.ModuleType("client_settings")
    client_settings.apply_saved_settings_to_env = lambda: None
    client_settings.get_effective_settings = lambda: {}
    modules["client_settings"] = client_settings

    whisper_config = types.ModuleType("whisper_config")
    whisper_config.WHISPER_SERVER = root / "whisper-server"
    whisper_config.WHISPER_MODEL_DIR = root / "models"
    modules["whisper_config"] = whisper_config

    pyqt = types.ModuleType("PyQt5")
    widgets = types.ModuleType("PyQt5.QtWidgets")
    widgets.QApplication = _DummyQApplication
    gui = types.ModuleType("PyQt5.QtGui")
    gui.QFont = _DummyQFont
    modules["PyQt5"] = pyqt
    modules["PyQt5.QtWidgets"] = widgets
    modules["PyQt5.QtGui"] = gui

    main_window = types.ModuleType("main_window")
    main_window.MainWindow = type("MainWindow", (), {"show": lambda self: None})
    main_window.apply_theme = lambda app: None
    modules["main_window"] = main_window

    for name in (
        "settings_patch",
        "gpu_patch",
        "output_patch",
        "error_log_patch",
        "title_fetch_patch",
        "playlist_patch",
    ):
        mod = types.ModuleType(name)
        mod.install = lambda: None
        modules[name] = mod

    return mock.patch.dict(sys.modules, modules)


def _load_app_module():
    root = Path(__file__).resolve().parents[1]
    with _install_app_import_stubs(root):
        spec = importlib.util.spec_from_file_location("app_shutdown_test", root / "app.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class _FakeManager:
    def __init__(self, name):
        self.name = name
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


class AppSidecarShutdownTests(unittest.TestCase):
    def test_shutdown_on_exit_stops_all_known_sidecars_by_default(self):
        app = _load_app_module()
        created = {}

        def build(name):
            manager = _FakeManager(name)
            created[name] = manager
            return manager

        app._build_qwen3_tts_manager = lambda: build("qwen")
        app._build_localization_manager = lambda: build("localization")
        app._build_whisper_manager = lambda: build("whisper")

        with mock.patch.dict(os.environ, {}, clear=True):
            app.shutdown_sidecars_on_exit()

        self.assertTrue(app._sidecars_shutdown_done)
        self.assertTrue(app._sidecars_shutting_down)
        self.assertEqual(created["qwen"].shutdown_calls, 1)
        self.assertEqual(created["localization"].shutdown_calls, 1)
        self.assertEqual(created["whisper"].shutdown_calls, 1)

        # The shutdown hook may be called both by aboutToQuit and after exec_;
        # the second call should be a no-op.
        app.shutdown_sidecars_on_exit()
        self.assertEqual(created["qwen"].shutdown_calls, 1)

    def test_shutdown_on_exit_can_be_disabled_with_environment_flag(self):
        app = _load_app_module()
        app._build_qwen3_tts_manager = lambda: _FakeManager("qwen")
        app._build_localization_manager = lambda: _FakeManager("localization")
        app._build_whisper_manager = lambda: _FakeManager("whisper")

        with mock.patch.dict(os.environ, {"V2S_STOP_SIDECARS_ON_EXIT": "false"}, clear=True):
            app.shutdown_sidecars_on_exit()

        self.assertTrue(app._sidecars_shutdown_done)
        self.assertFalse(app._sidecars_shutting_down)
        self.assertIsNone(app._qwen3_tts_manager)
        self.assertIsNone(app._localization_manager)
        self.assertIsNone(app._whisper_manager)


if __name__ == "__main__":
    unittest.main()
