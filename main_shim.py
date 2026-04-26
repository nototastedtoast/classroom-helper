"""
main_shim.py — PTT classroom assistant shim
Replicates the ZeroClaw PTT loop using Python libs.
AI backend: tries `zeroclaw agent -m` first; falls back to Groq → Ollama → Anthropic.
Skills are invoked as subprocesses — same argv contract as ZeroClaw native mode.

Dependencies (install once):
    pip install faster-whisper keyboard pyttsx3 anthropic python-dotenv sounddevice openai

Usage:
    set ZEROCLAW_WORKSPACE=E:\\generic codeing\\spcn_classroom
    python main_shim.py
    Hold RIGHT_CTRL to record, release to transcribe and respond.
"""

import os
import sys
import json
import subprocess
import threading
import tempfile
import wave
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False
    print("[shim] faster-whisper not installed. pip install faster-whisper")

try:
    import sounddevice as sd
    import numpy as np
    _AUDIO_AVAILABLE = True
except ImportError:
    _AUDIO_AVAILABLE = False
    print("[shim] sounddevice not installed. pip install sounddevice")

try:
    import keyboard
    _KEYBOARD_AVAILABLE = True
except ImportError:
    _KEYBOARD_AVAILABLE = False
    print("[shim] keyboard not installed. pip install keyboard")

try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except ImportError:
    _PYTTSX3_AVAILABLE = False
    print("[shim] pyttsx3 not installed. pip install pyttsx3")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

SOUL_PATH = Path("SOUL.md")
SKILLS_DIR = Path("skills")
ZEROCLAW_BIN = Path("zeroclaw.exe")
PTT_KEY = "right ctrl"
WHISPER_MODEL_SIZE = "large-v3"  # change to "medium" for faster dev startup
LANGUAGE = "vi"
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 1024

_SOUL_TEXT = SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else ""

_WHISPER: "WhisperModel | None" = None


def _load_whisper() -> "WhisperModel | None":
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER
    if not _WHISPER_AVAILABLE:
        return None
    print("[shim] Loading Whisper (first run downloads ~3 GB for large-v3)…")
    _WHISPER = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    print("[shim] Whisper ready.")
    return _WHISPER


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
_tts_engine = None


def _get_tts():
    global _tts_engine
    if _tts_engine is None and _PYTTSX3_AVAILABLE:
        _tts_engine = pyttsx3.init()
        _tts_engine.setProperty("rate", 155)
    return _tts_engine


def speak(text: str) -> None:
    print(f"[TTS] {text}")
    engine = _get_tts()
    if engine:
        engine.say(text)
        engine.runAndWait()


# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------
def _record_until_key_release() -> bytes | None:
    if not _AUDIO_AVAILABLE or not _KEYBOARD_AVAILABLE:
        return None
    frames: list[np.ndarray] = []
    print("[shim] Recording…")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK) as stream:
        while keyboard.is_pressed(PTT_KEY):
            block, _ = stream.read(CHUNK)
            frames.append(block)
    print("[shim] Done.")
    if not frames:
        return None
    return np.concatenate(frames, axis=0).tobytes()


def _pcm_to_wav(pcm: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return tmp.name


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
def transcribe(pcm: bytes) -> str:
    model = _load_whisper()
    if model is None:
        return ""
    wav_path = _pcm_to_wav(pcm)
    try:
        segments, _ = model.transcribe(wav_path, language=LANGUAGE)
        text = " ".join(s.text for s in segments).strip()
        print(f"[STT] {text}")
        return text
    finally:
        Path(wav_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# AI backend — priority: ZeroClaw → Groq → Ollama → Anthropic
# ---------------------------------------------------------------------------
def _ask_zeroclaw(message: str) -> str | None:
    if not ZEROCLAW_BIN.exists():
        return None
    env = {**os.environ, "ZEROCLAW_WORKSPACE": str(Path.cwd())}
    try:
        result = subprocess.run(
            [str(ZEROCLAW_BIN), "agent", "-m", message],
            capture_output=True, text=True, encoding="utf-8", timeout=30, env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _ask_groq(user_text: str, skill_context: str = "") -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        if skill_context:
            user_text = f"{user_text}\n\n[Skill context]:\n{skill_context}"
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[
                {"role": "system", "content": _SOUL_TEXT},
                {"role": "user", "content": user_text},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[shim] Groq error: {e}")
        return None


def _ask_ollama(user_text: str, skill_context: str = "") -> str | None:
    try:
        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
        if skill_context:
            user_text = f"{user_text}\n\n[Skill context]:\n{skill_context}"
        resp = client.chat.completions.create(
            model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            max_tokens=300,
            messages=[
                {"role": "system", "content": _SOUL_TEXT},
                {"role": "user", "content": user_text},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None


def _ask_anthropic(user_text: str, skill_context: str = "") -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key == "sk-ant-your-key-here":
        return None
    try:
        import anthropic  # noqa: PLC0415
        client = anthropic.Anthropic(api_key=api_key)
        if skill_context:
            user_text = f"{user_text}\n\n[Skill context]:\n{skill_context}"
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=_SOUL_TEXT,
            messages=[{"role": "user", "content": user_text}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"[shim] Anthropic error: {e}")
        return None


def ask_ai(user_text: str, skill_context: str = "") -> str:
    combined = user_text
    if skill_context:
        combined = f"{user_text}\n\n[Skill context]:\n{skill_context}"

    for fn, label in [
        (lambda: _ask_zeroclaw(combined), "zeroclaw"),
        (lambda: _ask_groq(user_text, skill_context), "groq"),
        (lambda: _ask_ollama(user_text, skill_context), "ollama"),
        (lambda: _ask_anthropic(user_text, skill_context), "anthropic"),
    ]:
        result = fn()
        if result:
            print(f"[shim] AI: {label}")
            return result

    return "Xin lỗi, không có dịch vụ AI nào khả dụng. Vui lòng kiểm tra GROQ_API_KEY hoặc khởi động Ollama."


# ---------------------------------------------------------------------------
# Skill routing — subprocess interface identical to ZeroClaw native invocation
# ---------------------------------------------------------------------------
_SKILL_MAP: dict[str, tuple[str, str | None]] = {
    # file opening
    "mở file": ("open_file", "extract_filename"),
    "mở tài liệu": ("open_file", "extract_filename"),
    # lesson recap
    "tóm tắt": ("recap_lesson", None),
    "ôn bài": ("recap_lesson", None),
    "hôm trước": ("recap_lesson", None),
    "lưu tiến độ": ("recap_lesson", "--save-start"),
    # today's schedule
    "hôm nay học gì": ("today_schedule", None),
    "lịch hôm nay": ("today_schedule", None),
    "học gì hôm nay": ("today_schedule", None),
    # slide control — pass full utterance as arg
    "tiếp theo": ("slide_control", "extract_command"),
    "slide tiếp": ("slide_control", "extract_command"),
    "trang tiếp": ("slide_control", "extract_command"),
    "quay lại": ("slide_control", "extract_command"),
    "slide trước": ("slide_control", "extract_command"),
    "trang trước": ("slide_control", "extract_command"),
    "đầu tiên": ("slide_control", "extract_command"),
    "slide ": ("slide_control", "extract_command"),   # "slide 5", "slide số..."
    "trang ": ("slide_control", "extract_command"),
}


def _run_skill(skill_name: str, arg: str | None) -> str:
    script = SKILLS_DIR / f"{skill_name}.py"
    if not script.exists():
        return f"Skill {skill_name} chưa được cài đặt."
    cmd = [sys.executable, str(script)]
    if arg:
        cmd.append(arg)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip() or result.stderr.strip()


def _detect_skill(text: str) -> tuple[str, str | None] | None:
    lower = text.lower()
    for trigger, (skill, arg_hint) in _SKILL_MAP.items():
        if trigger in lower:
            if arg_hint == "extract_filename":
                idx = lower.find(trigger) + len(trigger)
                filename = text[idx:].strip()
                return (skill, filename if filename else None)
            if arg_hint == "extract_command":
                # pass the full utterance so the skill can parse intent itself
                return (skill, text)
            return (skill, arg_hint)
    return None


# ---------------------------------------------------------------------------
# Turn processor
# ---------------------------------------------------------------------------
def _process_utterance(text: str) -> None:
    if not text:
        return

    skill_match = _detect_skill(text)
    if skill_match:
        skill_name, arg = skill_match
        raw = _run_skill(skill_name, arg)

        if skill_name == "recap_lesson" and raw.startswith("{"):
            try:
                ctx = json.loads(raw)
                if ctx.get("has_session"):
                    reply = ask_ai("Tóm tắt buổi học gần nhất cho tôi.", skill_context=raw)
                    _run_skill("recap_lesson", "--save-start")
                else:
                    reply = ctx.get("message", raw)
            except json.JSONDecodeError:
                reply = raw
        else:
            # today_schedule and slide_control return plain spoken strings directly
            reply = raw
    else:
        reply = ask_ai(text)

    speak(reply)


# ---------------------------------------------------------------------------
# Main PTT loop
# ---------------------------------------------------------------------------
def main() -> None:
    missing = []
    if not _WHISPER_AVAILABLE:
        missing.append("faster-whisper")
    if not _AUDIO_AVAILABLE:
        missing.append("sounddevice")
    if not _KEYBOARD_AVAILABLE:
        missing.append("keyboard")
    if not _PYTTSX3_AVAILABLE:
        missing.append("pyttsx3")
    if missing:
        print(f"[shim] Missing packages: {', '.join(missing)}")
        print("[shim] Install with: pip install " + " ".join(missing))
        print("[shim] Continuing in degraded mode (text-only Q&A if dependencies missing).")

    backend = "zeroclaw" if ZEROCLAW_BIN.exists() else "anthropic SDK"
    text_mode = "--text" in sys.argv or not _KEYBOARD_AVAILABLE or not _AUDIO_AVAILABLE
    print(f"[shim] AI backend: {backend}")
    if text_mode:
        print("[shim] Mode: text input")
    else:
        print(f"[shim] Mode: voice  (hold {PTT_KEY.upper()} to speak)")

    speak("Trợ lý lớp học đã sẵn sàng.")

    if text_mode:
        print("[shim] Type your message and press Enter. Ctrl+C to quit.")
        while True:
            try:
                text = input("You: ").strip()
                if text:
                    threading.Thread(target=_process_utterance, args=(text,), daemon=True).start()
                    time.sleep(0.5)
            except (KeyboardInterrupt, EOFError):
                print("\n[shim] Bye.")
                break
        return

    while True:
        keyboard.wait(PTT_KEY)
        pcm = _record_until_key_release()
        if pcm:
            text = transcribe(pcm)
            if text:
                threading.Thread(target=_process_utterance, args=(text,), daemon=True).start()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
