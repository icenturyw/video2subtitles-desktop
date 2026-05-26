import requests
import time
import json
import os
from pathlib import Path
from urllib.parse import urljoin


class WhisperApiClient:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or os.environ.get("WHISPER_SERVER_URL") or "http://127.0.0.1:8765").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("API_AUTH_KEY", "")
        self.session = requests.Session()
        self.session.timeout = (5, 120)

    def _headers(self):
        if self.api_key:
            return {"x-api-key": self.api_key}
        return {}

    def health_check(self):
        try:
            r = self.session.get(f"{self.base_url}/health", timeout=3)
            return r.status_code == 200
        except requests.ConnectionError:
            return False

    def get_server_info(self):
        try:
            r = self.session.get(f"{self.base_url}/health", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def transcribe_video(self, video_path, language=None, service="local", api_key=None):
        task_id = Path(video_path).stem.replace(" ", "_")
        payload = {
            "video_url": video_path,
            "language": language or "auto",
            "service": service,
        }
        if api_key:
            payload["api_key"] = api_key
        try:
            r = self.session.post(f"{self.base_url}/transcribe", json=payload, headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return r.json()
            return {"error": r.text}
        except Exception as e:
            return {"error": str(e)}

    def get_task_status(self, task_id):
        try:
            r = self.session.get(f"{self.base_url}/status/{task_id}", timeout=10)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def wait_for_result(self, task_id, progress_callback=None, poll_interval=1.0):
        while True:
            result = self.get_task_status(task_id)
            if result is None:
                return {"status": "error", "message": "无法连接到服务器"}
            status = result.get("status")
            progress = result.get("progress", 0)
            message = result.get("message", "")
            if progress_callback:
                progress_callback(progress, message, status)
            if status == "completed":
                return result
            if status == "error":
                return result
            time.sleep(poll_interval)

    def transcribe_url(self, video_url, language=None, service="local", api_key=None):
        payload = {
            "video_url": video_url,
            "language": language or "auto",
            "service": service,
        }
        if api_key:
            payload["api_key"] = api_key
        try:
            r = self.session.post(f"{self.base_url}/transcribe", json=payload, headers=self._headers(), timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                return {"error": "Whisper Server 鉴权失败：请在环境变量 API_AUTH_KEY 中配置正确密钥，或使用内置本地服务。"}
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except requests.exceptions.Timeout:
            return {"error": "请求超时，服务器可能繁忙"}
        except requests.exceptions.ConnectionError:
            return {"error": "无法连接到服务器，请检查本地 Whisper 服务是否已启动"}
        except Exception as e:
            return {"error": f"请求失败: {str(e)}"}

    def upload_file(self, file_path, language=None, service="local", api_key=None):
        task_id = Path(file_path).stem.replace(" ", "_")
        try:
            with open(file_path, "rb") as f:
                files = {"file": (Path(file_path).name, f, "video/mp4")}
                data = {"language": language or "auto", "service": service}
                if api_key:
                    data["api_key"] = api_key
                r = self.session.post(
                    f"{self.base_url}/upload", files=files, data=data, headers=self._headers(), timeout=30
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 401:
                    return {"error": "Whisper Server 鉴权失败：请检查 API_AUTH_KEY。"}
                return {"error": r.text}
        except Exception as e:
            return {"error": str(e)}

    def save_srt(self, subtitles, output_path):
        lines = []
        for i, sub in enumerate(subtitles, 1):
            start = self._format_time(sub.get("start", 0))
            end = self._format_time(sub.get("end", 0))
            text = sub.get("text", "")
            translation = sub.get("translation", "")
            if translation:
                text = f"{text}\n{translation}"
            lines.append(str(i))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
        content = "\n".join(lines)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    def save_vtt(self, subtitles, output_path):
        lines = ["WEBVTT", ""]
        for i, sub in enumerate(subtitles, 1):
            start = self._format_time(sub.get("start", 0)).replace(",", ".")
            end = self._format_time(sub.get("end", 0)).replace(",", ".")
            text = sub.get("text", "")
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
        content = "\n".join(lines)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    def save_txt(self, subtitles, output_path):
        lines = [sub.get("text", "") for sub in subtitles]
        content = "\n".join(lines)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def _format_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def get_supported_formats():
        return {
            ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
            ".m4v", ".mpg", ".mpeg", ".3gp", ".ogv", ".ts",
        }

    @staticmethod
    def scan_video_files(paths):
        videos = []
        exts = WhisperApiClient.get_supported_formats()
        for p in paths:
            p = Path(p)
            if p.is_file() and p.suffix.lower() in exts:
                videos.append(p)
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and f.suffix.lower() in exts:
                        videos.append(f)
        return videos
