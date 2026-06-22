from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_whisper_server():
    path = Path(__file__).resolve().parent.parent / "whisper-server" / "main.py"
    spec = importlib.util.spec_from_file_location("whisper_server_main_for_tests", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_caption_language_candidates_prefers_simplified_chinese():
    server = _load_whisper_server()

    assert server._caption_language_candidates("zh")[:2] == ["zh-Hans", "zh-CN"]
    assert server._caption_language_candidates("zh-CN")[0] == "zh-Hans"
    assert server._caption_language_candidates("auto") == []


def test_unload_model_clears_resident_whisper_model(monkeypatch):
    server = _load_whisper_server()
    monkeypatch.setattr(server, "MODEL", object())
    monkeypatch.setattr(server, "MODEL_KEY", ("base", "cuda"))

    assert server._unload_model() is True
    assert server.MODEL is None
    assert server.MODEL_KEY is None
    assert server._unload_model() is False


def test_select_caption_track_prefers_manual_then_json3():
    server = _load_whisper_server()
    info = {
        "subtitles": {
            "zh-Hans": [
                {"ext": "vtt", "url": "manual-vtt"},
                {"ext": "json3", "url": "manual-json3"},
            ]
        },
        "automatic_captions": {
            "zh-Hans": [{"ext": "json3", "url": "auto-json3"}],
        },
    }

    track, language = server._select_caption_track(info, "zh")

    assert language == "zh-Hans"
    assert track["url"] == "manual-json3"


def test_parse_youtube_json3_events():
    server = _load_whisper_server()
    payload = {
        "events": [
            {"tStartMs": 1000, "dDurationMs": 2400, "segs": [{"utf8": "你"}, {"utf8": "好"}]},
            {"tStartMs": 4000, "dDurationMs": 1000, "segs": [{"utf8": "\n"}]},
        ]
    }

    subtitles = server._parse_youtube_json3(payload)

    assert subtitles == [{"start": 1.0, "end": 3.5, "text": "你好"}]


def test_parse_youtube_vtt_blocks():
    server = _load_whisper_server()
    text = """WEBVTT

00:00:01.000 --> 00:00:02.500
<c>第一句</c>

note
00:00:03.000 --> 00:00:04.000
第二句
"""

    subtitles = server._parse_youtube_vtt(text)

    assert subtitles == [
        {"start": 1.0, "end": 2.6, "text": "第一句"},
        {"start": 3.0, "end": 4.1, "text": "第二句"},
    ]


def test_task_id_includes_requested_language():
    server = _load_whisper_server()
    url = "https://www.youtube.com/watch?v=cJpxcGIbseg"

    assert server._task_id_from_url(url, "auto") == "cJpxcGIbseg"
    assert server._task_id_from_url(url, "zh") == "cJpxcGIbseg_zh"


def test_youtube_cookie_detection_requires_youtube_or_google_domain(tmp_path):
    server = _load_whisper_server()

    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n.jianying.com\tTRUE\t/\tFALSE\t0\tsession\tabc\n",
        encoding="utf-8",
    )

    assert server._has_youtube_cookies(cookies) is False

    cookies.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tabc\n",
        encoding="utf-8",
    )

    assert server._has_youtube_cookies(cookies) is True


def test_login_error_message_mentions_rejected_existing_youtube_cookies(tmp_path, monkeypatch):
    server = _load_whisper_server()
    monkeypatch.setattr(server, "SERVER_DIR", tmp_path)

    (tmp_path / "cookies.txt").write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tabc\n",
        encoding="utf-8",
    )

    message = server._classify_ytdlp_error("Sign in to confirm you're not a bot")

    assert "拒绝了当前 cookies.txt" in message
    assert "重新" in message


def test_login_error_message_reports_cookie_file_without_youtube_login(tmp_path, monkeypatch):
    server = _load_whisper_server()
    monkeypatch.setattr(server, "SERVER_DIR", tmp_path)

    (tmp_path / "cookies.txt").write_text(
        "# Netscape HTTP Cookie File\n.jianying.com\tTRUE\t/\tFALSE\t0\tsession\tabc\n",
        encoding="utf-8",
    )

    message = server._classify_ytdlp_error("Sign in to confirm you're not a bot")

    assert "不包含 YouTube/Google" in message
