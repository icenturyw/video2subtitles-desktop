"""Direct local whisper transcription via faster-whisper."""
import json
import os
import subprocess
from pathlib import Path

from whisper_config import (
    APP_DIR,
    WHISPER_MODEL_DIR,
    WHISPER_SERVER,
    find_python_executable,
)


HELPER_DIR = APP_DIR / ".cache"
TRANSCRIBE_SCRIPT = HELPER_DIR / "transcribe_local.py"


def _hidden_subprocess_kwargs():
    """Hide child console windows on Windows.

    When the desktop app is launched through pythonw/start.bat, starting a
    console executable such as python.exe may otherwise flash an empty black
    window. Keep stdout/stderr pipes intact while suppressing that window.
    """
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _create_transcribe_script():
    """Write the transcribe helper script into the project cache directory."""
    HELPER_DIR.mkdir(parents=True, exist_ok=True)
    WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    script = r'''import sys, json, os
from pathlib import Path


def emit(payload):
    print(json.dumps(payload))
    sys.stdout.flush()


whisper_server_dir = os.environ.get("WHISPER_SERVER_DIR", "").strip()
if whisper_server_dir:
    server_path = Path(whisper_server_dir)
    if server_path.exists():
        sys.path.insert(0, str(server_path))

try:
    from faster_whisper import WhisperModel
except ImportError as exc:
    emit({
        "type": "error",
        "message": "未安装 faster-whisper，请运行 pip install -r requirements.txt，或配置可用的 Whisper Server。"
    })
    raise SystemExit(2) from exc


def split_text(text, max_len=25):
    import re
    if len(text) <= max_len:
        return [text]
    sentence_endings = r'([\u3002\uff01\uff1f\uff1b.!?;])'
    minor_punctuations = r'([\uff0c,\u3001])'
    sentences = re.split(sentence_endings, text)
    result = []
    current = ""
    i = 0
    while i < len(sentences):
        part = sentences[i]
        if i + 1 < len(sentences) and re.match(sentence_endings, sentences[i + 1]):
            part += sentences[i + 1]
            i += 2
        else:
            i += 1
        if len(part) > max_len:
            sub_parts = re.split(minor_punctuations, part)
            sub_current = ""
            j = 0
            while j < len(sub_parts):
                sub_part = sub_parts[j]
                if j + 1 < len(sub_parts) and re.match(minor_punctuations, sub_parts[j + 1]):
                    sub_part += sub_parts[j + 1]
                    j += 2
                else:
                    j += 1
                if len(sub_current) + len(sub_part) > max_len and sub_current:
                    result.append(sub_current.strip())
                    sub_current = sub_part
                else:
                    sub_current += sub_part
            if sub_current:
                if len(sub_current) > max_len:
                    while len(sub_current) > max_len:
                        result.append(sub_current[:max_len].strip())
                        sub_current = sub_current[max_len:]
                if sub_current.strip():
                    current = sub_current
        else:
            if len(current) + len(part) > max_len and current:
                result.append(current.strip())
                current = part
            else:
                current += part
    if current:
        if len(current) > max_len:
            while len(current) > max_len:
                result.append(current[:max_len].strip())
                current = current[max_len:]
        if current.strip():
            result.append(current.strip())
    return [r for r in result if r] or [text]


def transcribe(audio_path, language=None):
    emit({"type": "status", "progress": 10, "message": "Loading Whisper model..."})

    model_name_or_path = os.environ.get("WHISPER_MODEL_PATH", "").strip() or os.environ.get("MODEL_SIZE", "base").strip() or "base"
    model_dir = os.environ.get("WHISPER_MODEL_DIR", "").strip()
    device = os.environ.get("DEVICE", "cpu")
    compute_type = os.environ.get("COMPUTE_TYPE", "int8")

    model_kwargs = {"device": device, "compute_type": compute_type}
    if model_dir:
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        model_kwargs["download_root"] = model_dir

    model = WhisperModel(model_name_or_path, **model_kwargs)

    emit({"type": "status", "progress": 20, "message": "Model loaded, transcribing..."})

    segments, info = model.transcribe(
        audio_path,
        language=None if not language or language == 'auto' else language,
        beam_size=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=1000),
        initial_prompt="以下是普通话的句子，请用简体中文。"
    )

    detected_lang = info.language
    total_duration = info.duration

    emit({"type": "status", "progress": 25, "message": f"Detected: {detected_lang}, generating subtitles..."})

    subtitles = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if len(text) > 25:
            split_parts = split_text(text)
            duration = segment.end - segment.start
            total_chars = sum(len(p) for p in split_parts) or 1
            current_start = segment.start
            for part in split_parts:
                part_len = len(part)
                part_duration = (part_len / total_chars) * duration
                part_end = current_start + part_duration
                subtitles.append({'start': round(current_start, 2), 'end': round(part_end + 0.1, 2), 'text': part})
                current_start = part_end
        else:
            subtitles.append({'start': round(segment.start, 2), 'end': round(segment.end + 0.1, 2), 'text': text})
        if total_duration and total_duration > 0:
            progress = min(98, 25 + int((segment.end / total_duration) * 73))
            if int(progress) % 5 == 0:
                emit({"type": "status", "progress": int(progress), "message": f"Transcribing: {int(segment.end)}s / {int(total_duration)}s ({int(progress)}%)..."})

    emit({"type": "complete", "subtitles": subtitles, "language": detected_lang})


if __name__ == "__main__":
    audio_path = sys.argv[1]
    language = sys.argv[2] if len(sys.argv) > 2 else "auto"
    transcribe(audio_path, language)
'''

    with open(TRANSCRIBE_SCRIPT, "w", encoding="utf-8") as f:
        f.write(script)


class LocalWhisperTranscriber:
    def __init__(self):
        _create_transcribe_script()

    def transcribe(self, audio_path, language="auto", progress_callback=None):
        env = os.environ.copy()
        env.setdefault("WHISPER_SERVER_DIR", str(WHISPER_SERVER))
        env.setdefault("WHISPER_MODEL_DIR", str(WHISPER_MODEL_DIR))

        cmd = [find_python_executable(), str(TRANSCRIBE_SCRIPT), str(audio_path), language]
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "env": env,
        }
        popen_kwargs.update(_hidden_subprocess_kwargs())
        process = subprocess.Popen(cmd, **popen_kwargs)

        subtitles = []
        detected_lang = "unknown"
        error_message = ""

        for line in iter(process.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                msg_type = data.get("type", "")
                if msg_type == "status":
                    progress = data.get("progress", 0)
                    message = data.get("message", "")
                    if progress_callback:
                        progress_callback(progress, message, "processing")
                elif msg_type == "complete":
                    subtitles = data.get("subtitles", [])
                    detected_lang = data.get("language", "unknown")
                    if progress_callback:
                        progress_callback(100, f"Done! {len(subtitles)} subtitles", "completed")
                elif msg_type == "error":
                    error_message = data.get("message", "")
                    if progress_callback:
                        progress_callback(0, error_message, "error")
            except json.JSONDecodeError:
                continue

        process.wait()

        if process.returncode != 0:
            stderr = process.stderr.read()
            error_msg = error_message or (stderr[-300:] if stderr else "Unknown error")
            if progress_callback:
                progress_callback(0, error_msg, "error")
            return [error_msg], "error"

        return subtitles, detected_lang
