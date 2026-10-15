"""JARVIS Neural Voice & Sound Engine (V6)
Provides authentic Paul Bettany-style British RP vocal synthesis (en-GB-RyanNeural),
Kurdish Sorani (کوردیی سۆرانی) phonetic & neural speech synthesis, and offline Windows SAPI5 fallback.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TTS_CACHE_DIR = ROOT / "data" / "tts_cache"
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

VOICE_EN = os.getenv("JARVIS_VOICE_EN", "en-GB-RyanNeural")
VOICE_KU = os.getenv("JARVIS_VOICE_KU", "ar-IQ-BasselNeural")
VOICE_AR = os.getenv("JARVIS_VOICE_AR", "ar-IQ-BasselNeural")
PITCH_EN = os.getenv("JARVIS_VOICE_PITCH", "-4Hz")
RATE_EN = os.getenv("JARVIS_VOICE_RATE", "+5%")

# Kurdish Sorani -> Arabic/Phonetic script adaptation for clear neural pronunciation
_KURDISH_PHONETIC_MAP = {
    "ۆ": "و",
    "ێ": "ي",
    "ە": "ه",
    "ڕ": "ر",
    "ڵ": "ل",
    "پ": "ب",
    "چ": "تش",
    "ژ": "ج",
    "گ": "ك",
    "ڤ": "ف",
    "ھ": "ه",
    "ی": "ي",
    "ک": "ك",
}


def detect_script(text: str) -> str:
    """Detects whether text is Kurdish Sorani ('ku'), Arabic ('ar'), or English/Latin ('en')."""
    if re.search(r"[ۆەێڕڵپچژگڤھ]|(?:گەورەم|سڵاو|فەرموو|بەخێربێیت|کۆمپیوتەر|بکەرەوە|مۆبایل|پڕۆتۆکۆڵ)", text):
        return "ku"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    return "en"


def adapt_kurdish_for_neural_tts(text: str) -> str:
    """Adapts Kurdish Sorani text so neural Arabic/Middle-Eastern male voices pronounce every syllable clearly."""
    out = text
    # Common Kurdish words phonetic tuning for crisp pronunciation
    word_fixes = {
        "گەورەم": "گَوْرَم",
        "سڵاو": "سلاو",
        "جاڕڤس": "جارفس",
        "جارڤیس": "جارفس",
        "بەخێربێیتەوە": "بَخير بيته وه",
        "بەخێربێیت": "بَخير بيت",
        "فەرموو": "فَرمو",
        "لە خزمەتدام": "له خِزمَتدام",
        "چۆنیت": "تشونيت",
        "باشە": "باشه",
        "تەواو": "تَواو",
        "ئامادەیە": "ئامادَيه",
        "سوپاس": "سوباس",
    }
    for k, v in word_fixes.items():
        out = out.replace(k, v)
    for k_char, ar_char in _KURDISH_PHONETIC_MAP.items():
        out = out.replace(k_char, ar_char)
    return out


def clean_speech_text(text: str) -> str:
    """Strips markdown, code blocks, and URLs so JARVIS speaks cleanly."""
    t = re.sub(r"```.*?```", " Code block generated on screen, Sir. ", text, flags=re.S)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"https?://\S+", "link", t)
    t = re.sub(r"[*_#~>\[\]\(\)]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:900]


async def synthesize_speech(
    text: str,
    voice_override: str = "",
    pitch: str = "",
    rate: str = "",
) -> tuple[bytes, str]:
    """Synthesizes speech audio (MP3 or WAV) with caching for zero-latency playback."""
    cleaned = clean_speech_text(text)
    if not cleaned:
        return b"", "audio/mpeg"

    lang = detect_script(cleaned)
    use_pitch = pitch or PITCH_EN
    use_rate = rate or RATE_EN

    if voice_override:
        voice = voice_override
        tts_text = adapt_kurdish_for_neural_tts(cleaned) if lang == "ku" else cleaned
    elif lang == "ku":
        voice = VOICE_KU
        tts_text = adapt_kurdish_for_neural_tts(cleaned)
        use_pitch = "-2Hz"
        use_rate = "+2%"
    elif lang == "ar":
        voice = VOICE_AR
        tts_text = cleaned
        use_pitch = "-2Hz"
        use_rate = "+2%"
    else:
        voice = VOICE_EN
        tts_text = cleaned

    cache_key = hashlib.sha256(f"{voice}|{use_pitch}|{use_rate}|{tts_text}".encode("utf-8")).hexdigest()[:20]
    cached_mp3 = TTS_CACHE_DIR / f"{cache_key}.mp3"
    if cached_mp3.is_file() and cached_mp3.stat().st_size > 200:
        return cached_mp3.read_bytes(), "audio/mpeg"

    # 1. Try edge-tts (Microsoft Neural Ryan British RP / Bassel Iraqi)
    try:
        import edge_tts
        communicate = edge_tts.Communicate(tts_text, voice=voice, pitch=use_pitch, rate=use_rate)
        await communicate.save(str(cached_mp3))
        if cached_mp3.is_file() and cached_mp3.stat().st_size > 200:
            return cached_mp3.read_bytes(), "audio/mpeg"
    except Exception:
        pass

    # 2. Fallback: Windows SAPI5 British/Male WAV synthesizer via PowerShell
    if platform.system().lower() == "windows":
        cached_wav = TTS_CACHE_DIR / f"{cache_key}.wav"
        if cached_wav.is_file() and cached_wav.stat().st_size > 200:
            return cached_wav.read_bytes(), "audio/wav"
        try:
            safe_txt = cleaned.replace("'", " ").replace('"', " ")
            ps_script = (
                "Add-Type -AssemblyName System.Speech; "
                "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$voices = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo }; "
                "$uk = $voices | Where-Object { $_.Culture.Name -eq 'en-GB' -and $_.Gender -eq 'Male' } | Select-Object -First 1; "
                "if (-not $uk) { $uk = $voices | Where-Object { $_.Culture.Name -eq 'en-GB' } | Select-Object -First 1 }; "
                "if (-not $uk) { $uk = $voices | Where-Object { $_.Gender -eq 'Male' } | Select-Object -First 1 }; "
                "if ($uk) { $synth.SelectVoice($uk.Name) }; "
                "$synth.Rate = 1; "
                f"$synth.SetOutputToWaveFile('{cached_wav}'); "
                f"$synth.Speak('{safe_txt}'); "
                "$synth.Dispose();"
            )
            await asyncio.to_thread(
                subprocess.run,
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                timeout=12,
                creationflags=0x08000000,
            )
            if cached_wav.is_file() and cached_wav.stat().st_size > 200:
                return cached_wav.read_bytes(), "audio/wav"
        except Exception:
            pass

    return b"", "audio/mpeg"
