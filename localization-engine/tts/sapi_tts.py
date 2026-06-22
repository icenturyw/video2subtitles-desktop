from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from process_utils import hidden_subprocess_kwargs
from tts.base import TTSCache, TTSResult, TTSUnavailableError


class SapiTTSProvider:
    """Windows Speech API based TTS provider.

    This provider is intentionally local-only. It gives the pipeline a
    dependable fallback when online providers or the Qwen3-TTS service are not
    available.
    """

    supports_concurrency = False

    def __init__(self, cache: Optional[TTSCache] = None):
        self._cache = cache
        self._voice_cache: Optional[List[Dict[str, str]]] = None

    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        if os.name != "nt":
            return []
        if self._voice_cache is None:
            result = _run_powershell(
                r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voices = $s.GetInstalledVoices() | ForEach-Object {
    [PSCustomObject]@{
      name = $_.VoiceInfo.Name
      locale = $_.VoiceInfo.Culture.Name
      gender = [string]$_.VoiceInfo.Gender
    }
  }
  $voices | ConvertTo-Json -Compress
} finally {
  $s.Dispose()
}
""",
                timeout=30,
            )
            if result.returncode != 0:
                self._voice_cache = []
            else:
                self._voice_cache = _parse_voice_json(result.stdout)
        if not language:
            return list(self._voice_cache)
        lang = language.lower()
        base = lang.split("-", 1)[0]
        return [
            voice for voice in self._voice_cache
            if str(voice.get("locale", "")).lower().startswith((lang, base))
        ]

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        if os.name != "nt":
            raise TTSUnavailableError("Windows SAPI TTS is only available on Windows")

        text = " ".join(str(text or "").split())
        if not text:
            raise TTSUnavailableError("Cannot synthesize empty text")

        output_path = output_path.resolve()
        voice_key = voice or "default"
        if self._cache:
            cached = self._cache.get(text, voice_key, language)
            if cached:
                duration = _get_wav_duration(cached)
                if duration > 0:
                    import shutil
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=duration)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        text_path = output_path.with_suffix(output_path.suffix + ".txt")
        text_path.write_text(text, encoding="utf-8")

        rate = _clamp_int(options.get("sapi_rate", 0), -10, 10)
        volume = _clamp_int(options.get("sapi_volume", 100), 0, 100)
        timeout = int(options.get("timeout", 120) or 120)

        first_error: Optional[Exception] = None
        try:
            _synthesize_with_com(text, output_path, voice, rate, volume)
        except Exception as exc:
            first_error = exc
        duration = _get_wav_duration(output_path)

        if duration <= 0:
            output_path.unlink(missing_ok=True)
            try:
                result = _run_powershell(
                    _synthesize_script(text_path, output_path, voice, rate, volume),
                    timeout=timeout,
                )
            finally:
                text_path.unlink(missing_ok=True)

            if result.returncode != 0:
                detail = (result.stderr or result.stdout or str(first_error or "")).strip()[-1200:]
                raise TTSUnavailableError(f"Windows SAPI synthesis failed: {detail}")

            duration = _get_wav_duration(output_path)
        else:
            text_path.unlink(missing_ok=True)

        if duration <= 0:
            raise TTSUnavailableError("Windows SAPI did not create valid audio")

        if self._cache:
            self._cache.put(text, voice_key, language, output_path)

        return TTSResult(output_path=output_path, duration_seconds=duration)


def _synthesize_script(
    text_path: Path,
    output_path: Path,
    voice: str,
    rate: int,
    volume: int,
) -> str:
    text_literal = _ps_single_quote(str(text_path))
    output_literal = _ps_single_quote(str(output_path))
    voice_literal = _ps_single_quote(str(voice or ""))
    return f"""
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText({text_literal}, [System.Text.Encoding]::UTF8)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
  $voice = {voice_literal}
  if ($voice) {{
    $match = $s.GetInstalledVoices() | Where-Object {{
      $_.VoiceInfo.Name -eq $voice -or $_.VoiceInfo.Culture.Name -eq $voice
    }} | Select-Object -First 1
    if ($match) {{
      $s.SelectVoice($match.VoiceInfo.Name)
    }}
  }}
  $s.Rate = {rate}
  $s.Volume = {volume}
  $s.SetOutputToWaveFile({output_literal})
  $s.Speak($text)
}} finally {{
  $s.Dispose()
}}
"""


def _synthesize_with_com(
    text: str,
    output_path: Path,
    voice: str,
    rate: int,
    volume: int,
) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    stream = None
    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        if voice and voice != "default":
            for token in speaker.GetVoices():
                description = str(token.GetDescription())
                if description == voice:
                    speaker.Voice = token
                    break

        speaker.Rate = rate
        speaker.Volume = volume

        audio_format = win32com.client.Dispatch("SAPI.SpAudioFormat")
        audio_format.Type = 22  # 22 kHz, 16-bit, mono PCM.
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format = audio_format
        stream.Open(str(output_path), 3, False)  # SSFMCreateForWrite.
        speaker.AudioOutputStream = stream
        speaker.Speak(text)
    finally:
        if stream is not None:
            try:
                stream.Close()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _run_powershell(script: str, *, timeout: int) -> subprocess.CompletedProcess:
    script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ps1", encoding="utf-8-sig", delete=False
        ) as handle:
            script_path = Path(handle.name)
            handle.write(script)
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            **hidden_subprocess_kwargs(),
        )
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_voice_json(raw: str) -> List[Dict[str, str]]:
    import json

    raw = (raw or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    voices = []
    for item in data:
        if isinstance(item, dict) and item.get("name"):
            voices.append({
                "name": str(item.get("name", "")),
                "locale": str(item.get("locale", "")),
                "gender": str(item.get("gender", "")),
            })
    return voices


def _clamp_int(value, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = low
    return max(low, min(high, parsed))


def _get_wav_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0
