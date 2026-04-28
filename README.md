# Trợ Lý Lớp Học

A voice-controlled AI teaching assistant overlay for Windows. Sits on top of Zoom, Microsoft Teams, or any meeting software — always visible, always ready. Designed for teachers and students, including those with mobility or visual impairments.

Built on [ZeroClaw](https://zeroclaw.dev) as the AI backbone, with Vietnamese speech recognition and text-to-speech.

---

## What it does

| Feature | How to use |
|---|---|
| Ask any question | Type or speak — AI answers in Vietnamese |
| Open a file by name | Say "mở file [tên file]" or click it in the File tab |
| Today's schedule | Quick button or say "hôm nay học gì" |
| Lesson recap | Say "ôn bài" to hear where last session ended |
| Save progress note | Memory tab → type → Lưu |
| Import timetable from photo | File tab → Chọn ảnh → AI reads and saves it |
| Live lesson transcript | Ghi âm tab → Bắt đầu ghi → rolling timestamped text |
| End-of-lesson summary | Ghi âm tab → Tóm tắt → AI summarises topics and homework |
| Save transcript | Ghi âm tab → Lưu → saves to `C:/ClassroomFiles/transcript_….txt` |
| Push-to-talk (global) | Hold **F9** anywhere on screen |
| Mute/unmute voice | Click **🔊** in the title bar |

---

## Quickstart

**1. Clone or download this folder.**

**2. Add your API key.**

Create a `.env` file in the project root:

```
GROQ_API_KEY=gsk_your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com). No credit card required.

**3. Run `start.bat` → choose option 2 (Install dependencies).**

This creates a Python virtual environment and installs everything automatically.

**4. Run `start.bat` → choose option 1 (Run app).**

The overlay appears on the right edge of your screen.

---

## Requirements

- Windows 10 or 11
- Python 3.10+ (3.12 recommended; 3.14 supported)
- Internet connection (for AI and TTS)
- A free [Groq API key](https://console.groq.com)

---

## How voice works

```
Hold F9  →  microphone opens
Release F9  →  Whisper transcribes (Vietnamese)
               →  ZeroClaw / Groq answers
                  →  edge-tts speaks the reply aloud (vi-VN-HoaiMyNeural)
```

Whisper (`large-v3`) runs locally on CPU. The first load takes ~30 seconds to download the model.

---

## AI fallback chain

The app tries each provider in order until one succeeds:

1. **ZeroClaw** — local binary, uses your Groq key
2. **Groq** — `llama-3.3-70b-versatile`, free tier, fast
3. **Ollama** — local model if installed
4. **Anthropic** — Claude, if `ANTHROPIC_API_KEY` is set

The green dot (●) in the title bar turns red if no AI is reachable.

---

## Skills

Skills are small Python scripts in the `skills/` folder. The AI calls them when it detects a matching phrase in Vietnamese.

| Skill | Trigger phrases | What it does |
|---|---|---|
| `open_file` | "mở file …", "mở tài liệu …" | Fuzzy-searches Desktop, Documents, Downloads, D:/, E:/ and opens the file |
| `recap_lesson` | "ôn bài", "tóm tắt", "hôm trước" | Reads session memory and recent files, returns a lesson recap |
| `today_schedule` | "lịch hôm nay", "hôm nay học gì" | Reads `C:/ClassroomFiles/schedule.json` and announces today's subjects |

### Adding a skill

Create `skills/my_skill.py` with a `run(arg)` function that returns a Vietnamese string. The app will find it automatically.

---

## Timetable import

Go to the **File** tab → click **Chọn ảnh** → select a photo of your timetable (PNG, JPG, WebP).

The AI (Groq vision or Anthropic) reads the image and saves the result to `C:/ClassroomFiles/schedule.json`. After that, "hôm nay học gì" will answer correctly.

---

## File structure

```
spcn_classroom/
├── overlay.py          # Main app (CustomTkinter window)
├── app.py              # Flask backend (AI routing, skill dispatch)
├── config.toml         # ZeroClaw workspace config
├── SOUL.md             # AI persona (calm, Vietnamese, classroom-scoped)
├── start.bat           # Launcher menu
├── .env                # API keys (not committed)
├── skills/
│   ├── open_file.py
│   ├── recap_lesson.py
│   └── today_schedule.py
├── vocab/
│   └── vi_classroom.txt   # Whisper hints for Vietnamese classroom terms
└── zeroclaw.exe           # ZeroClaw binary
```

---

## Accessibility

This tool is designed with accessibility in mind:

- **Mobility impairment** — global F9 push-to-talk requires no mouse interaction; works while any other application has focus
- **Visual impairment** — every AI response is spoken aloud automatically via Microsoft neural TTS (`vi-VN-HoaiMyNeural`)
- **Low tech literacy** — voice commands in natural Vietnamese; no file explorer needed to open documents

---

## Building a standalone .exe

Run `start.bat` → option 3. PyInstaller bundles everything into `dist/TroLyLopHoc/`. Copy your `.env` into that folder before distributing.

Build time: 5–15 minutes. The resulting folder is ~500 MB due to the Whisper model.

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | API keys (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`) |
| `config.toml` | ZeroClaw provider, model, memory, autonomy settings |
| `SOUL.md` | AI persona — edit to change tone, scope, or language |

To change the push-to-talk key, edit `_PTT_KEY = "f9"` near the top of `overlay.py`.

To change the TTS voice, edit `_TTS_VOICE` in the same file. Available Vietnamese voices: `vi-VN-HoaiMyNeural` (female), `vi-VN-NamMinhNeural` (male).
