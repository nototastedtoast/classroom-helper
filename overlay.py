"""
overlay.py — Trợ Lý Lớp Học
Teaching assistant overlay built on ZeroClaw.
Always-on-top, voice-first, accessibility-ready.

Features:
  - ZeroClaw AI backbone (Vietnamese, Groq)
  - Push-to-talk: hold F9 anywhere (global hotkey)
  - TTS: speaks every reply via edge-tts (vi-VN-HoaiMyNeural)
  - File open by voice/click
  - Lesson memory, timetable import from photo
"""
import os
import re
import socket
import threading
import time
import tempfile
import wave
from pathlib import Path

import customtkinter as ctk
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Flask backend ─────────────────────────────────────────────────────────────
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

_PORT = _free_port()
_BASE = f"http://127.0.0.1:{_PORT}"

_flask_thread: threading.Thread | None = None
_flask_lock = threading.Lock()

def _run_flask() -> None:
    try:
        from app import app as _app  # noqa: PLC0415
        _app.run(port=_PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"[Flask] crashed: {e}")

def _ensure_flask() -> None:
    global _flask_thread
    with _flask_lock:
        if _flask_thread is None or not _flask_thread.is_alive():
            _flask_thread = threading.Thread(target=_run_flask, daemon=True, name="flask")
            _flask_thread.start()

_ensure_flask()
for _ in range(100):
    try:
        requests.get(f"{_BASE}/zc/status", timeout=1)
        break
    except Exception:
        time.sleep(0.3)

# ── Audio / STT ───────────────────────────────────────────────────────────────
try:
    import sounddevice as sd
    import numpy as np
    _AUDIO_OK = True
except ImportError:
    _AUDIO_OK = False

try:
    from faster_whisper import WhisperModel
    _WHISPER_OK = True
except ImportError:
    _WHISPER_OK = False

_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None and _WHISPER_OK:
        _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    return _whisper_model

# ── TTS ───────────────────────────────────────────────────────────────────────
try:
    import edge_tts as _edge_tts
    _TTS_OK = True
except ImportError:
    _TTS_OK = False

_TTS_VOICE = "vi-VN-HoaiMyNeural"
_tts_lock  = threading.Lock()   # one voice at a time


def _mci(cmd: str) -> None:
    """Send a command to Windows MCI (plays MP3 via built-in codec, no extra libs)."""
    import ctypes  # noqa: PLC0415
    ctypes.windll.winmm.mciSendStringW(cmd, None, 0, None)


def _play_mp3(path: str) -> None:
    safe = str(path).replace('"', "")
    _mci(f'open "{safe}" type mpegvideo alias _zctts')
    _mci("play _zctts wait")
    _mci("close _zctts")


def _stop_tts() -> None:
    try:
        _mci("stop _zctts")
        _mci("close _zctts")
    except Exception:
        pass


def _clean_for_tts(text: str) -> str:
    text = re.sub(r"[\U0001F000-\U0001FFFF\U00002600-\U000027BF -⁯]", "", text)
    text = re.sub(r"[*_`#]", "", text)
    text = re.sub(r"^[\s]*[•\-]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700]


# ── Global PTT hotkey ─────────────────────────────────────────────────────────
try:
    import keyboard as _kb
    _KB_OK = True
except ImportError:
    _KB_OK = False

_PTT_KEY = "f9"

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG      = "#0f0f1e"
BG2     = "#16162a"
ACCENT  = "#3730a3"
ACC2    = "#818cf8"
TEXT    = "#e2e8f0"
DIM     = "#4b5563"
BORDER  = "#1e293b"
BOT_BG  = "#1a1a30"
USER_BG = "#252060"
MIC_ON  = "#7f1d1d"


# ── Main window ───────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 380, sh - 60
        self.geometry(f"{w}x{h}+{sw - w - 8}+30")
        self.title("Trợ Lý Lớp Học")
        self.configure(fg_color=BG)
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.wm_attributes("-alpha", 0.96)
        self.resizable(False, True)

        self._dx = self._dy = 0
        self._recording = False
        self._rec_frames: list = []
        self._tts_muted = False
        self._pinned = True
        self._transcript_running = False
        self._transcript_lines: list[str] = []

        self._build()
        self._setup_ptt()
        self._poll_status()
        self.after(600, lambda: self._bot(
            "Xin chào! Tôi là trợ lý lớp học. Giữ F9 để nói, hoặc nhập câu hỏi bên dưới."
        ))

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        self._titlebar()
        self._tabbar()
        self._quickbar()

        self._content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._content.pack(fill="both", expand=True)

        self._panels: dict[str, ctk.CTkFrame] = {}
        self._build_chat()
        self._build_files()
        self._build_memory()
        self._build_transcript()
        self._switch("chat")

    def _titlebar(self):
        bar = ctk.CTkFrame(self, fg_color=BG2, height=44, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        bar.bind("<ButtonPress-1>", self._drag_start)
        bar.bind("<B1-Motion>",     self._drag_move)

        self._dot = ctk.CTkLabel(bar, text="●", text_color="#4ade80", font=("", 10), width=14)
        self._dot.pack(side="left", padx=(10, 4))
        self._dot.bind("<ButtonPress-1>", self._drag_start)
        self._dot.bind("<B1-Motion>",     self._drag_move)

        lbl = ctk.CTkLabel(bar, text="Trợ Lý Lớp Học",
                           font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT)
        lbl.pack(side="left")
        lbl.bind("<ButtonPress-1>", self._drag_start)
        lbl.bind("<B1-Motion>",     self._drag_move)

        # Window controls (right → left order)
        for icon, cmd in [("✕", self.destroy), ("—", self._minimize), ("⧉", self._pin_toggle)]:
            ctk.CTkButton(bar, text=icon, width=32, height=28, fg_color="transparent",
                          hover_color=BORDER, text_color=DIM, font=("", 13),
                          command=cmd).pack(side="right", padx=2)

        # TTS mute toggle
        self._spk_btn = ctk.CTkButton(
            bar, text="🔊", width=32, height=28, fg_color="transparent",
            hover_color=BORDER, text_color=DIM, font=("", 14),
            command=self._toggle_tts)
        self._spk_btn.pack(side="right", padx=2)

    def _tabbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG2, height=36, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._tabs: dict[str, ctk.CTkButton] = {}
        for label, key in [("💬 Hỏi đáp", "chat"), ("📂 File", "files"),
                           ("🧠 Ký ức", "memory"), ("📝 Ghi âm", "transcript")]:
            b = ctk.CTkButton(bar, text=label, height=30, width=84,
                              fg_color=ACCENT if key == "chat" else "transparent",
                              hover_color=ACCENT, text_color=TEXT,
                              font=ctk.CTkFont(size=11), corner_radius=6,
                              command=lambda k=key: self._switch(k))
            b.pack(side="left", padx=2, pady=3)
            self._tabs[key] = b

    def _quickbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG, height=34, corner_radius=0)
        bar.pack(fill="x", padx=6, pady=(4, 0))
        bar.pack_propagate(False)
        for txt, cmd in [
            ("📅 Lịch hôm nay", "hôm nay học gì"),
            ("📖 Ôn bài",       "ôn bài hôm trước"),
            ("💾 Lưu tiến độ",  "lưu tiến độ"),
        ]:
            ctk.CTkButton(bar, text=txt, height=26,
                          fg_color=BORDER, hover_color=ACCENT, text_color=TEXT,
                          font=ctk.CTkFont(size=11), corner_radius=13,
                          command=lambda c=cmd: self._send(c)).pack(side="left", padx=2)
        ctk.CTkLabel(bar, text="F9=nói", font=ctk.CTkFont(size=9),
                     text_color=DIM).pack(side="right", padx=6)

    # ── Tab switching ─────────────────────────────────────────────────────────
    def _switch(self, key: str):
        for p in self._panels.values():
            p.pack_forget()
        self._panels[key].pack(fill="both", expand=True)
        for k, b in self._tabs.items():
            b.configure(fg_color=ACCENT if k == key else "transparent")
        if key == "files":
            self._load_files()
        if key == "memory":
            self._load_memory()
        if key == "transcript":
            self._update_trans_buttons()

    # ── Chat panel ────────────────────────────────────────────────────────────
    def _build_chat(self):
        p = ctk.CTkFrame(self._content, fg_color=BG, corner_radius=0)
        self._panels["chat"] = p

        self._chatbox = ctk.CTkTextbox(p, fg_color=BG, text_color=TEXT,
                                       font=ctk.CTkFont(size=12), wrap="word",
                                       state="disabled", corner_radius=0)
        self._chatbox.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        self._stt_lbl = ctk.CTkLabel(p, text="", text_color=ACC2,
                                     font=ctk.CTkFont(size=11, slant="italic"),
                                     wraplength=350, anchor="w")
        self._stt_lbl.pack(fill="x", padx=10, pady=(2, 0))

        ibar = ctk.CTkFrame(p, fg_color=BG2, corner_radius=12, height=48)
        ibar.pack(fill="x", padx=6, pady=6)
        ibar.pack_propagate(False)

        self._mic = ctk.CTkButton(ibar, text="🎙", width=38, height=38,
                                  fg_color=BORDER, hover_color=MIC_ON,
                                  text_color=TEXT, font=("", 15), corner_radius=19,
                                  command=self._toggle_mic)
        self._mic.pack(side="left", padx=(6, 3), pady=5)

        self._entry = ctk.CTkEntry(ibar, fg_color="transparent", border_width=0,
                                   text_color=TEXT, font=ctk.CTkFont(size=13),
                                   placeholder_text="Nhập câu hỏi hoặc nói tên file…",
                                   placeholder_text_color=DIM)
        self._entry.pack(side="left", fill="both", expand=True, padx=4)
        self._entry.bind("<Return>", lambda e: self._send_input())

        ctk.CTkButton(ibar, text="➤", width=38, height=38,
                      fg_color=ACCENT, hover_color=ACC2,
                      text_color="#fff", font=("", 14), corner_radius=19,
                      command=self._send_input).pack(side="right", padx=(3, 6), pady=5)

    def _bot(self, text: str):
        self._chatbox.configure(state="normal")
        self._chatbox.tag_config("bot", background=BOT_BG, foreground=TEXT,
                                 spacing1=5, spacing3=5, lmargin1=8, lmargin2=8, rmargin=8)
        self._chatbox.insert("end", f"  🤖  {text}\n\n", "bot")
        self._chatbox.configure(state="disabled")
        self._chatbox.see("end")
        self._speak(text)

    def _user(self, text: str):
        self._chatbox.configure(state="normal")
        self._chatbox.tag_config("user", background=USER_BG, foreground=TEXT,
                                 spacing1=5, spacing3=5, lmargin1=8, lmargin2=8, rmargin=8)
        self._chatbox.insert("end", f"  👤  {text}\n\n", "user")
        self._chatbox.configure(state="disabled")
        self._chatbox.see("end")

    def _send(self, msg: str):
        if not msg.strip():
            return
        self._user(msg)
        self._switch("chat")
        threading.Thread(target=self._do_send, args=(msg,), daemon=True).start()

    def _send_input(self):
        msg = self._entry.get().strip()
        if not msg:
            return
        self._entry.delete(0, "end")
        self._send(msg)

    def _do_send(self, msg: str):
        try:
            r = requests.post(f"{_BASE}/chat", json={"message": msg}, timeout=60)
            reply = r.json().get("reply", "?")
        except requests.exceptions.ConnectionError:
            _ensure_flask()
            reply = "Backend đang khởi động lại. Vui lòng thử lại sau vài giây."
        except Exception as e:
            reply = f"Lỗi: {e}"
        self.after(0, lambda: self._bot(reply))

    # ── TTS ───────────────────────────────────────────────────────────────────
    def _toggle_tts(self):
        self._tts_muted = not self._tts_muted
        self._spk_btn.configure(text="🔇" if self._tts_muted else "🔊")
        if self._tts_muted:
            threading.Thread(target=_stop_tts, daemon=True).start()

    def _speak(self, text: str):
        if self._tts_muted or not _TTS_OK:
            return
        clean = _clean_for_tts(text)
        if not clean:
            return
        threading.Thread(target=self._do_speak, args=(clean,), daemon=True).start()

    def _do_speak(self, text: str):
        import asyncio  # noqa: PLC0415
        with _tts_lock:
            _stop_tts()
            loop = asyncio.new_event_loop()
            tmp = None
            try:
                async def _gen(t: str, path: str) -> None:
                    comm = _edge_tts.Communicate(t, voice=_TTS_VOICE)
                    await comm.save(path)

                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    tmp = f.name
                loop.run_until_complete(_gen(text, tmp))
                _play_mp3(tmp)
            except Exception as e:
                print(f"[TTS] {e}")
            finally:
                loop.close()
                if tmp:
                    Path(tmp).unlink(missing_ok=True)

    # ── Global PTT ────────────────────────────────────────────────────────────
    def _setup_ptt(self):
        if not _KB_OK:
            return
        _kb.on_press_key(_PTT_KEY,   lambda _: self.after(0, self._ptt_press))
        _kb.on_release_key(_PTT_KEY, lambda _: self.after(0, self._ptt_release))

    def _ptt_press(self):
        if not self._recording:
            _stop_tts()
            self._start_mic()

    def _ptt_release(self):
        if self._recording:
            self._stop_mic()

    # ── Files panel ───────────────────────────────────────────────────────────
    def _build_files(self):
        p = ctk.CTkFrame(self._content, fg_color=BG, corner_radius=0)
        self._panels["files"] = p

        hdr = ctk.CTkFrame(p, fg_color=BG, height=36)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Tài liệu gần đây", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=ACC2).pack(side="left")
        ctk.CTkButton(hdr, text="↻", width=30, height=26, fg_color=BORDER,
                      hover_color=ACCENT, text_color=TEXT,
                      command=self._load_files).pack(side="right")

        imp = ctk.CTkFrame(p, fg_color=BG2, corner_radius=8)
        imp.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(imp, text="📅 Nhập thời khóa biểu",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT).pack(side="left", padx=10, pady=8)
        ctk.CTkButton(imp, text="Chọn ảnh", width=80, height=28,
                      fg_color=ACCENT, hover_color=ACC2, text_color="#fff",
                      font=ctk.CTkFont(size=11),
                      command=self._import_schedule).pack(side="right", padx=8, pady=6)

        sbar = ctk.CTkFrame(p, fg_color=BG, height=34)
        sbar.pack(fill="x", padx=8, pady=(0, 4))
        sbar.pack_propagate(False)
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter_files())
        ctk.CTkEntry(sbar, textvariable=self._search_var, fg_color=BG2, border_color=BORDER,
                     text_color=TEXT, font=ctk.CTkFont(size=12),
                     placeholder_text="Tìm file…", placeholder_text_color=DIM).pack(fill="x")

        self._file_scroll = ctk.CTkScrollableFrame(p, fg_color=BG2, corner_radius=8)
        self._file_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._all_files: list[dict] = []

    def _load_files(self):
        threading.Thread(target=self._fetch_files, daemon=True).start()

    def _fetch_files(self):
        try:
            data  = requests.get(f"{_BASE}/files", timeout=10).json()
            files = data.get("files", [])
        except Exception:
            files = []
        self._all_files = files
        self.after(0, lambda: self._render_files(files))

    def _filter_files(self):
        q = self._search_var.get().lower()
        filtered = [f for f in self._all_files if q in f["name"].lower()] if q else self._all_files
        self._render_files(filtered)

    def _render_files(self, files: list):
        for w in self._file_scroll.winfo_children():
            w.destroy()
        if not files:
            ctk.CTkLabel(self._file_scroll, text="Không có file nào.",
                         text_color=DIM, font=ctk.CTkFont(size=11)).pack(pady=20)
            return
        icons = {".pptx": "📊", ".ppt": "📊", ".pdf": "📄", ".docx": "📝",
                 ".doc": "📝", ".txt": "📃", ".xlsx": "📈", ".png": "🖼",
                 ".jpg": "🖼", ".mp4": "🎬", ".mp3": "🎵"}
        for f in files:
            row = ctk.CTkFrame(self._file_scroll, fg_color=BORDER, corner_radius=7, height=36)
            row.pack(fill="x", pady=2)
            row.pack_propagate(False)
            ico = icons.get(f.get("ext", ""), "📁")
            ctk.CTkLabel(row, text=ico, font=("", 13), width=26).pack(side="left", padx=(6, 2))
            name_lbl = ctk.CTkLabel(row, text=f["name"], font=ctk.CTkFont(size=11),
                                    text_color=TEXT, anchor="w")
            name_lbl.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(row, text=f.get("modified", ""), font=ctk.CTkFont(size=9),
                         text_color=DIM).pack(side="right", padx=6)
            for widget in (row, name_lbl):
                widget.bind("<Button-1>", lambda e, n=f["name"]: self._open_file(n))

    def _import_schedule(self):
        from tkinter import filedialog  # noqa: PLC0415
        path = filedialog.askopenfilename(
            title="Chọn ảnh thời khóa biểu",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self._switch("chat")
        self._bot("Đang đọc thời khóa biểu từ ảnh… (có thể mất vài giây)")
        threading.Thread(target=self._do_import_schedule, args=(path,), daemon=True).start()

    def _do_import_schedule(self, path: str):
        try:
            with open(path, "rb") as f:
                img_bytes = f.read()
            fname = Path(path).name
            r = requests.post(
                f"{_BASE}/import-schedule",
                files={"file": (fname, img_bytes, "image/png")},
                timeout=60,
            )
            data  = r.json()
            reply = data.get("reply") or data.get("error", "Không rõ lỗi.")
            lines = data.get("summary", [])
            if lines:
                reply += "\n" + "\n".join(f"  • {l}" for l in lines)
        except Exception as e:
            reply = f"Lỗi import: {e}"
        self.after(0, lambda: self._bot(reply))

    def _open_file(self, name: str):
        self._switch("chat")
        self._user(f"Mở file: {name}")
        threading.Thread(target=self._do_open, args=(name,), daemon=True).start()

    def _do_open(self, name: str):
        try:
            r = requests.post(f"{_BASE}/open-file", json={"name": name}, timeout=10)
            reply = r.json().get("reply", "?")
        except Exception as e:
            reply = str(e)
        self.after(0, lambda: self._bot(reply))

    # ── Memory panel ─────────────────────────────────────────────────────────
    def _build_memory(self):
        p = ctk.CTkFrame(self._content, fg_color=BG, corner_radius=0)
        self._panels["memory"] = p

        hdr = ctk.CTkFrame(p, fg_color=BG, height=36)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Ký ức ZeroClaw", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=ACC2).pack(side="left")
        ctk.CTkButton(hdr, text="↻", width=30, height=26, fg_color=BORDER,
                      hover_color=ACCENT, text_color=TEXT,
                      command=self._load_memory).pack(side="right")

        self._mem_box = ctk.CTkTextbox(p, fg_color=BG2, text_color=TEXT,
                                       font=ctk.CTkFont(size=11), wrap="word",
                                       state="disabled", corner_radius=8)
        self._mem_box.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        note_bar = ctk.CTkFrame(p, fg_color=BG, height=40)
        note_bar.pack(fill="x", padx=8, pady=(0, 8))
        note_bar.pack_propagate(False)
        self._note_entry = ctk.CTkEntry(note_bar, fg_color=BG2, border_color=BORDER,
                                        text_color=TEXT, font=ctk.CTkFont(size=12),
                                        placeholder_text="Ghi chú nhanh…",
                                        placeholder_text_color=DIM)
        self._note_entry.pack(side="left", fill="both", expand=True)
        self._note_entry.bind("<Return>", lambda e: self._save_note())
        ctk.CTkButton(note_bar, text="Lưu", width=54, height=32, fg_color=ACCENT,
                      hover_color=ACC2, text_color="#fff",
                      command=self._save_note).pack(side="right", padx=(4, 0))

    def _load_memory(self):
        threading.Thread(target=self._fetch_memory, daemon=True).start()

    def _fetch_memory(self):
        try:
            data     = requests.get(f"{_BASE}/zc/memory", timeout=10).json()
            sessions = data.get("sessions", [])
            lines = []
            for s in sessions:
                lines.append(f"  {s['date']}")
                if s["summary"]:    lines.append(f"   {s['summary']}")
                if s["last_file"]:  lines.append(f"    {s['last_file']}")
                if s["checkpoint"]: lines.append(f"    {s['checkpoint']}")
                lines.append("")
            text = "\n".join(lines) or "Chưa có ký ức nào."
        except Exception as e:
            text = f"Lỗi: {e}"
        self.after(0, lambda: self._set_mem(text))

    def _set_mem(self, text: str):
        self._mem_box.configure(state="normal")
        self._mem_box.delete("1.0", "end")
        self._mem_box.insert("end", text)
        self._mem_box.configure(state="disabled")

    def _save_note(self):
        note = self._note_entry.get().strip()
        if not note:
            return
        self._note_entry.delete(0, "end")
        threading.Thread(target=lambda: requests.post(
            f"{_BASE}/zc/memory/save", json={"summary": note}, timeout=10
        ), daemon=True).start()
        self._bot(f"Đã lưu ghi chú: {note}")
        self.after(1200, self._load_memory)

    # ── Microphone ────────────────────────────────────────────────────────────
    def _toggle_mic(self):
        if self._recording:
            self._stop_mic()
        else:
            self._start_mic()

    def _start_mic(self):
        if not _AUDIO_OK:
            self._bot("Cần cài sounddevice: python -m pip install sounddevice numpy")
            return
        self._recording = True
        self._rec_frames = []
        self._mic.configure(fg_color=MIC_ON, text="⏹")
        self._stt_lbl.configure(text="  Đang nghe…")
        threading.Thread(target=self._record, daemon=True).start()

    def _record(self):
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1024) as s:
            while self._recording:
                block, _ = s.read(1024)
                self._rec_frames.append(block)

    def _stop_mic(self):
        self._recording = False
        self._mic.configure(fg_color=BORDER, text="🎙")
        self._stt_lbl.configure(text="  Đang nhận dạng…")
        threading.Thread(target=self._transcribe, daemon=True).start()

    def _transcribe(self):
        if not self._rec_frames or not _WHISPER_OK:
            self.after(0, lambda: self._stt_lbl.configure(text=""))
            return
        try:
            pcm = np.concatenate(self._rec_frames, axis=0).tobytes()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm)
            model = _get_whisper()
            if model is None:
                self.after(0, lambda: self._stt_lbl.configure(text="Whisper chưa sẵn sàng."))
                return
            segs, _ = model.transcribe(tmp, language="vi")
            text = " ".join(s.text for s in segs).strip()
            Path(tmp).unlink(missing_ok=True)
            self.after(0, lambda: self._stt_lbl.configure(text=""))
            if text:
                self.after(0, lambda: self._send(text))
        except Exception as e:
            self.after(0, lambda: self._stt_lbl.configure(text=f"Lỗi STT: {e}"))

    # ── Transcript panel ─────────────────────────────────────────────────────
    def _build_transcript(self):
        p = ctk.CTkFrame(self._content, fg_color=BG, corner_radius=0)
        self._panels["transcript"] = p

        hdr = ctk.CTkFrame(p, fg_color=BG, height=36)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Ghi âm buổi học", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=ACC2).pack(side="left")
        ctk.CTkButton(hdr, text="Xóa", width=40, height=26, fg_color=BORDER,
                      hover_color="#7f1d1d", text_color=TEXT, font=ctk.CTkFont(size=11),
                      command=self._clear_transcript).pack(side="right", padx=(2, 0))
        ctk.CTkButton(hdr, text="💾 Lưu", width=62, height=26, fg_color=BORDER,
                      hover_color=ACCENT, text_color=TEXT, font=ctk.CTkFont(size=11),
                      command=self._save_transcript).pack(side="right", padx=2)

        self._trans_box = ctk.CTkTextbox(p, fg_color=BG2, text_color=TEXT,
                                         font=ctk.CTkFont(size=11), wrap="word",
                                         state="disabled", corner_radius=8)
        self._trans_box.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        ctrl = ctk.CTkFrame(p, fg_color=BG, height=40)
        ctrl.pack(fill="x", padx=8, pady=(0, 8))
        ctrl.pack_propagate(False)

        self._trans_btn = ctk.CTkButton(
            ctrl, text="▶  Bắt đầu ghi", height=34,
            fg_color=ACCENT, hover_color=ACC2, text_color="#fff",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_transcript)
        self._trans_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(ctrl, text="🤖 Tóm tắt", width=88, height=34,
                      fg_color=BORDER, hover_color=ACCENT, text_color=TEXT,
                      font=ctk.CTkFont(size=11),
                      command=self._summarize_transcript).pack(side="right")

    def _toggle_transcript(self):
        if self._transcript_running:
            self._stop_transcript()
        else:
            self._start_transcript()

    def _start_transcript(self):
        if not _AUDIO_OK:
            self._bot("Cần cài sounddevice để ghi âm.")
            return
        if not _WHISPER_OK:
            self._bot("Cần cài faster-whisper để phiên âm.")
            return
        self._transcript_running = True
        self._update_trans_buttons()
        threading.Thread(target=self._record_transcript, daemon=True).start()

    def _stop_transcript(self):
        self._transcript_running = False
        self._update_trans_buttons()

    def _update_trans_buttons(self):
        if self._transcript_running:
            self._trans_btn.configure(text="⏹  Dừng ghi", fg_color=MIC_ON, hover_color="#991b1b")
        else:
            self._trans_btn.configure(text="▶  Bắt đầu ghi", fg_color=ACCENT, hover_color=ACC2)

    def _record_transcript(self):
        SR          = 16000
        CHUNK_SECS  = 10
        target      = CHUNK_SECS * SR
        buffer: list = []
        count       = 0

        with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=1024) as stream:
            while self._transcript_running:
                block, _ = stream.read(1024)
                buffer.append(block.copy())
                count += len(block)
                if count >= target:
                    chunk = np.concatenate(buffer, axis=0)
                    buffer, count = [], 0
                    threading.Thread(target=self._process_chunk,
                                     args=(chunk,), daemon=True).start()
            # Flush remaining audio on stop
            if buffer:
                chunk = np.concatenate(buffer, axis=0)
                threading.Thread(target=self._process_chunk,
                                 args=(chunk,), daemon=True).start()

    def _process_chunk(self, pcm: "np.ndarray"):
        try:
            timestamp = time.strftime("%H:%M:%S")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm.tobytes())
            model = _get_whisper()
            if model is None:
                return
            segs, _ = model.transcribe(tmp, language="vi")
            text = " ".join(s.text for s in segs).strip()
            Path(tmp).unlink(missing_ok=True)
            if text:
                line = f"[{timestamp}]  {text}"
                self._transcript_lines.append(line)
                self.after(0, lambda ln=line: self._append_trans(ln))
        except Exception as e:
            print(f"[Transcript] {e}")

    def _append_trans(self, line: str):
        self._trans_box.configure(state="normal")
        self._trans_box.insert("end", line + "\n")
        self._trans_box.configure(state="disabled")
        self._trans_box.see("end")

    def _clear_transcript(self):
        self._transcript_lines.clear()
        self._trans_box.configure(state="normal")
        self._trans_box.delete("1.0", "end")
        self._trans_box.configure(state="disabled")

    def _save_transcript(self):
        if not self._transcript_lines:
            return
        folder = Path("E:/TroLyLopHoc")
        folder.mkdir(parents=True, exist_ok=True)
        fname = f"transcript_{time.strftime('%Y-%m-%d_%H-%M')}.txt"
        (folder / fname).write_text("\n".join(self._transcript_lines), encoding="utf-8")
        self._bot(f"Đã lưu transcript: {fname}")
        self._switch("chat")

    def _summarize_transcript(self):
        if not self._transcript_lines:
            self._bot("Chưa có nội dung ghi âm nào để tóm tắt.")
            self._switch("chat")
            return
        full = "\n".join(self._transcript_lines)
        prompt = (
            f"Đây là transcript buổi học hôm nay:\n\n{full}\n\n"
            "Hãy tóm tắt ngắn gọn: các chủ đề đã học, thuật ngữ quan trọng, "
            "và bài tập về nhà (nếu có)."
        )
        self._switch("chat")
        self._user("Tóm tắt buổi học")
        threading.Thread(target=self._do_send, args=(prompt,), daemon=True).start()

    # ── Window controls ───────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _minimize(self):
        self.iconify()

    def _pin_toggle(self):
        self._pinned = not self._pinned
        self.wm_attributes("-topmost", self._pinned)

    # ── Status dot ────────────────────────────────────────────────────────────
    def _poll_status(self):
        threading.Thread(target=self._fetch_status, daemon=True).start()
        self.after(12000, self._poll_status)

    def _fetch_status(self):
        try:
            s = requests.get(f"{_BASE}/zc/status", timeout=3).json()
            has_ai = s.get("groq_configured") or s.get("ollama_reachable") or s.get("anthropic_configured")
            col = "#4ade80" if has_ai else "#f87171"
            self.after(0, lambda: self._dot.configure(text_color=col))
        except Exception:
            # Backend unreachable — restart it and show red dot
            _ensure_flask()
            self.after(0, lambda: self._dot.configure(text_color="#f87171"))


if __name__ == "__main__":
    App().mainloop()
