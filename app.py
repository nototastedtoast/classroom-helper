"""
app.py — Web frontend for the classroom assistant.
Shares the same skill subprocess interface as main_shim.py.

Run:
    python app.py
    Open http://localhost:5000
"""
import os
import sys
import json
import base64
import subprocess
from pathlib import Path
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, abort
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

SKILLS_DIR   = Path("skills")
FILES_FOLDER = Path("C:/ClassroomFiles")

_PREVIEWABLE_TEXT = {".txt", ".md", ".csv", ".json", ".py", ".html", ".xml", ".toml"}
_PREVIEWABLE_MEDIA = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".wav"}
ZEROCLAW_BIN = Path("zeroclaw.exe")
SOUL_PATH    = Path("SOUL.md")
_SOUL_TEXT   = SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else ""

# ---------------------------------------------------------------------------
# Skill routing (identical contract to main_shim)
# ---------------------------------------------------------------------------
_SKILL_MAP: dict[str, tuple[str, str | None]] = {
    "mở file":        ("open_file",       "extract_filename"),
    "mở tài liệu":    ("open_file",       "extract_filename"),
    "tóm tắt":        ("recap_lesson",    None),
    "ôn bài":         ("recap_lesson",    None),
    "hôm trước":      ("recap_lesson",    None),
    "lưu tiến độ":    ("recap_lesson",    "--save-start"),
    "hôm nay học gì": ("today_schedule",  None),
    "lịch hôm nay":   ("today_schedule",  None),
    "học gì hôm nay": ("today_schedule",  None),
}


def _run_skill(skill_name: str, arg: str | None) -> str:
    script = SKILLS_DIR / f"{skill_name}.py"
    if not script.exists():
        return f"Skill {skill_name} chưa được cài đặt."
    cmd = [sys.executable, str(script)]
    if arg:
        cmd.append(arg)
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=15
    )
    return result.stdout.strip() or result.stderr.strip()


def _detect_skill(text: str) -> tuple[str, str | None] | None:
    lower = text.lower()
    for trigger, (skill, hint) in _SKILL_MAP.items():
        if trigger in lower:
            if hint == "extract_filename":
                idx = lower.find(trigger) + len(trigger)
                filename = text[idx:].strip()
                return (skill, filename if filename else None)
            if hint == "extract_command":
                return (skill, text)
            return (skill, hint)
    return None


# ---------------------------------------------------------------------------
# AI backends
# ---------------------------------------------------------------------------
def _ask_zeroclaw(message: str) -> str | None:
    if not ZEROCLAW_BIN.exists():
        return None
    env = {
        **os.environ,
        "ZEROCLAW_WORKSPACE": str(Path.cwd()),
        "ZEROCLAW_API_KEY": os.environ.get("GROQ_API_KEY", ""),
    }
    try:
        r = subprocess.run(
            [str(ZEROCLAW_BIN), "agent", "-m", message],
            capture_output=True, text=True, encoding="utf-8", timeout=30, env=env,
        )
        if r.returncode == 0 and r.stdout.strip():
            return _strip_zc_logs(r.stdout)
    except Exception:
        pass
    return None


def _strip_zc_logs(raw: str) -> str | None:
    """Remove ZeroClaw log lines (contain ANSI codes / zeroclaw:: markers), keep AI reply."""
    import re  # noqa: PLC0415
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    lines = []
    for line in raw.splitlines():
        clean = ansi.sub("", line).strip()
        # Skip lines that look like structured log entries
        if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", clean):
            continue
        if not clean:
            continue
        lines.append(clean)
    return "\n".join(lines).strip() or None


def _ask_groq(user_text: str, context: str = "") -> str | None:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return None
    try:
        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        prompt = f"{user_text}\n\n[Context]:\n{context}" if context else user_text
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "system", "content": _SOUL_TEXT},
                      {"role": "user",   "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[app] Groq error: {e}")
        return None


def _ask_ollama(user_text: str, context: str = "") -> str | None:
    try:
        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
        prompt = f"{user_text}\n\n[Context]:\n{context}" if context else user_text
        resp = client.chat.completions.create(
            model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            max_tokens=400,
            messages=[{"role": "system", "content": _SOUL_TEXT},
                      {"role": "user",   "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None


def _ask_anthropic(user_text: str, context: str = "") -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key == "sk-ant-your-key-here":
        return None
    try:
        import anthropic  # noqa: PLC0415
        client = anthropic.Anthropic(api_key=key)
        prompt = f"{user_text}\n\n[Context]:\n{context}" if context else user_text
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_SOUL_TEXT,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"[app] Anthropic error: {e}")
        return None


def ask_ai(user_text: str, context: str = "") -> str:
    combined = f"{user_text}\n\n[Context]:\n{context}" if context else user_text
    for fn, label in [
        (lambda: _ask_zeroclaw(combined),       "zeroclaw"),
        (lambda: _ask_groq(user_text, context), "groq"),
        (lambda: _ask_ollama(user_text, context), "ollama"),
        (lambda: _ask_anthropic(user_text, context), "anthropic"),
    ]:
        result = fn()
        if result:
            print(f"[app] AI: {label}")
            return result
    return "Xin lỗi, không có dịch vụ AI nào khả dụng."


# ---------------------------------------------------------------------------
# Process a single message — same logic as main_shim._process_utterance
# ---------------------------------------------------------------------------
def process_message(text: str) -> str:
    skill_match = _detect_skill(text)
    if not skill_match:
        return ask_ai(text)

    skill_name, arg = skill_match
    raw = _run_skill(skill_name, arg)

    if skill_name == "recap_lesson" and raw.startswith("{"):
        try:
            ctx = json.loads(raw)
            if ctx.get("has_session"):
                reply = ask_ai("Tóm tắt buổi học gần nhất cho tôi.", context=raw)
                _run_skill("recap_lesson", "--save-start")
                return reply
            return ctx.get("message", raw)
        except json.JSONDecodeError:
            pass

    return raw


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data    = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    reply = process_message(message)
    return jsonify({"reply": reply})


_ALLOWED_EXT = {
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
    ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mp3", ".wav",
}

_SEARCH_ROOTS = [
    FILES_FOLDER,
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
]


def _scan_files(roots: list[Path], max_depth: int = 2) -> list[dict]:
    seen: set[Path] = set()
    results: list[tuple] = []

    def _walk(path: Path, depth: int):
        if depth == 0 or not path.exists():
            return
        try:
            for p in path.iterdir():
                if p.is_file() and p.suffix.lower() in _ALLOWED_EXT and p not in seen:
                    seen.add(p)
                    results.append((p.stat().st_mtime, p))
                elif p.is_dir() and not p.name.startswith("."):
                    _walk(p, depth - 1)
        except PermissionError:
            pass

    for root in roots:
        _walk(root, max_depth)

    results.sort(key=lambda x: x[0], reverse=True)
    files = []
    for mtime, p in results[:80]:
        size = p.stat().st_size
        files.append({
            "name": p.name,
            "ext": p.suffix.lower(),
            "size": f"{size/1024/1024:.1f} MB" if size > 1_048_576 else f"{size//1024} KB",
            "modified": datetime.fromtimestamp(mtime).strftime("%d/%m %H:%M"),
            "path": str(p.parent),
            "previewable": p.suffix.lower() in _PREVIEWABLE_TEXT | _PREVIEWABLE_MEDIA,
        })
    return files


@app.route("/files")
def list_files():
    FILES_FOLDER.mkdir(parents=True, exist_ok=True)
    return jsonify({"files": _scan_files(_SEARCH_ROOTS)})


@app.route("/open-file", methods=["POST"])
def open_file_route():
    name = (request.get_json(force=True).get("name") or "").strip()
    if not name:
        return jsonify({"error": "no filename"}), 400
    reply = _run_skill("open_file", name)
    return jsonify({"reply": reply})


@app.route("/preview/<path:filename>")
def preview_file(filename):
    path = FILES_FOLDER / filename
    if not path.exists() or not path.is_file():
        abort(404)
    # Safety: must stay inside FILES_FOLDER
    try:
        path.resolve().relative_to(FILES_FOLDER.resolve())
    except ValueError:
        abort(403)
    return send_file(path)


@app.route("/read/<path:filename>")
def read_file(filename):
    path = FILES_FOLDER / filename
    if not path.exists() or path.suffix.lower() not in _PREVIEWABLE_TEXT:
        abort(404)
    try:
        path.resolve().relative_to(FILES_FOLDER.resolve())
    except ValueError:
        abort(403)
    content = path.read_text(encoding="utf-8", errors="replace")
    return jsonify({"content": content})


@app.route("/upload", methods=["POST"])
def upload():
    FILES_FOLDER.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        dest = FILES_FOLDER / Path(f.filename).name
        f.save(dest)
        uploaded.append(dest.name)
    if not uploaded:
        return jsonify({"error": "Không có file nào được tải lên."}), 400
    names = ", ".join(uploaded)
    return jsonify({"reply": f"Đã lưu: {names}", "files": uploaded})


@app.route("/delete/<path:filename>", methods=["DELETE"])
def delete_file(filename):
    path = FILES_FOLDER / filename
    if not path.exists():
        return jsonify({"error": "File không tồn tại."}), 404
    try:
        path.resolve().relative_to(FILES_FOLDER.resolve())
    except ValueError:
        abort(403)
    path.unlink()
    return jsonify({"reply": f"Đã xóa {filename}"})


# ---------------------------------------------------------------------------
# ZeroClaw backbone routes
# ---------------------------------------------------------------------------

@app.route("/zc/status")
def zc_status():
    """Return current AI backend, ZeroClaw version, memory state."""
    status = {
        "zeroclaw_present": ZEROCLAW_BIN.exists(),
        "groq_configured": bool(os.environ.get("GROQ_API_KEY")),
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-ant")),
        "ollama_reachable": False,
        "memory_db": str(Path("classroom.db").resolve()),
        "memory_exists": Path("classroom.db").exists(),
        "files_folder": str(FILES_FOLDER),
        "skills": [p.stem for p in SKILLS_DIR.glob("*.py")] if SKILLS_DIR.exists() else [],
    }
    # Check Ollama
    try:
        import urllib.request  # noqa: PLC0415
        urllib.request.urlopen("http://localhost:11434", timeout=1)
        status["ollama_reachable"] = True
    except Exception:
        pass
    # Get ZeroClaw version if available
    if ZEROCLAW_BIN.exists():
        try:
            r = subprocess.run([str(ZEROCLAW_BIN), "--version"], capture_output=True, text=True, timeout=5)
            status["zeroclaw_version"] = r.stdout.strip() or r.stderr.strip()
        except Exception:
            status["zeroclaw_version"] = "unknown"
    return jsonify(status)


@app.route("/zc/memory")
def zc_memory():
    """Return recent session history from classroom.db."""
    db = Path("classroom.db")
    if not db.exists():
        return jsonify({"sessions": [], "error": "No memory database yet."})
    try:
        import sqlite3  # noqa: PLC0415
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT id, date, summary, last_file, checkpoint, started_at FROM sessions ORDER BY id DESC LIMIT 20"
        ).fetchall()
        conn.close()
        sessions = [
            {"id": r[0], "date": r[1], "summary": r[2] or "", "last_file": r[3] or "",
             "checkpoint": r[4] or "", "started_at": r[5] or ""}
            for r in rows
        ]
        return jsonify({"sessions": sessions})
    except Exception as e:
        return jsonify({"sessions": [], "error": str(e)})


@app.route("/zc/memory/save", methods=["POST"])
def zc_memory_save():
    """Save or update the latest session summary."""
    data     = request.get_json(force=True)
    summary  = (data.get("summary") or "").strip()
    last_file = (data.get("last_file") or "").strip()
    checkpoint = (data.get("checkpoint") or "").strip()
    if not summary:
        return jsonify({"error": "summary required"}), 400
    try:
        import sqlite3  # noqa: PLC0415
        from datetime import datetime as dt  # noqa: PLC0415
        conn = sqlite3.connect(str(Path("classroom.db")))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "date TEXT NOT NULL, summary TEXT, last_file TEXT, checkpoint TEXT, started_at TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions (date, summary, last_file, checkpoint, started_at) VALUES (?,?,?,?,?)",
            (dt.now().strftime("%Y-%m-%d"), summary, last_file, checkpoint, dt.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        conn.close()
        return jsonify({"reply": "Đã lưu ghi chú vào bộ nhớ."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/zc/shell", methods=["POST"])
def zc_shell():
    """Run an allowed shell command via ZeroClaw autonomy rules."""
    import tomllib  # noqa: PLC0415
    data = request.get_json(force=True)
    cmd  = (data.get("command") or "").strip()
    if not cmd:
        return jsonify({"error": "No command provided."}), 400

    # Load allowed_commands from config.toml
    allowed: list[str] = ["ls", "dir", "cat", "open"]
    config_path = Path("config.toml")
    if config_path.exists():
        try:
            cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
            allowed = cfg.get("autonomy", {}).get("allowed_commands", allowed)
        except Exception:
            pass

    base = cmd.split()[0].lower()
    if base not in [a.lower() for a in allowed]:
        return jsonify({"error": f"Command '{base}' not in allowed list: {allowed}"}), 403

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        output = result.stdout or result.stderr or "(no output)"
        return jsonify({"output": output, "returncode": result.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Command timed out."}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/zc/skills")
def zc_skills():
    """List all skills with their docstrings."""
    skills = []
    if SKILLS_DIR.exists():
        for p in sorted(SKILLS_DIR.glob("*.py")):
            doc = ""
            try:
                src = p.read_text(encoding="utf-8")
                # Extract first docstring
                import ast  # noqa: PLC0415
                tree = ast.parse(src)
                doc = (ast.get_docstring(tree) or "").split("\n")[0].strip()
            except Exception:
                pass
            skills.append({"name": p.stem, "file": p.name, "description": doc})
    return jsonify({"skills": skills})


@app.route("/zc/run-skill", methods=["POST"])
def zc_run_skill():
    """Run a skill by name with optional argument."""
    data = request.get_json(force=True)
    name = (data.get("skill") or "").strip()
    arg  = (data.get("arg") or "").strip() or None
    if not name:
        return jsonify({"error": "skill name required"}), 400
    output = _run_skill(name, arg)
    return jsonify({"reply": output})


@app.route("/zc/agent", methods=["POST"])
def zc_agent():
    """Send a message directly to ZeroClaw agent and stream the response."""
    data    = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message required"}), 400
    if not ZEROCLAW_BIN.exists():
        return jsonify({"error": "zeroclaw.exe not found."}), 404
    env = {**os.environ, "ZEROCLAW_WORKSPACE": str(Path.cwd())}
    try:
        r = subprocess.run(
            [str(ZEROCLAW_BIN), "agent", "-m", message],
            capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
        )
        return jsonify({"reply": r.stdout.strip() or r.stderr.strip()})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ZeroClaw timed out."}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/import-schedule", methods=["POST"])
def import_schedule():
    """
    Accept an image or PDF of a timetable.
    Send it to the AI vision model to extract the schedule.
    Save result to C:/ClassroomFiles/schedule.json.
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded."}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return jsonify({"error": f"Unsupported format '{ext}'. Upload a PNG, JPG, or WebP image."}), 400

    img_bytes  = f.read()
    img_b64    = base64.standard_b64encode(img_bytes).decode()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".webp": "image/webp",
        ".gif": "image/gif",  ".bmp": "image/bmp",
    }.get(ext, "image/png")

    prompt = """Look at this timetable image and extract the weekly schedule.
Return ONLY a valid JSON object — no markdown, no explanation.
Use these exact keys for days: thu_2 thu_3 thu_4 thu_5 thu_6 thu_7 chu_nhat
(thu_2 = Monday, thu_3 = Tuesday, ..., chu_nhat = Sunday)
Each value is a list of subject/topic strings for that day.
Example:
{
  "thu_2": ["Toán đại số", "Vật lý"],
  "thu_3": ["Ngữ văn", "Lịch sử"],
  "thu_4": [],
  "thu_5": ["Hóa học"],
  "thu_6": ["Sinh học", "Địa lý"],
  "thu_6": [],
  "chu_nhat": []
}
If a day has no classes, use an empty list.
Only include what you can clearly read from the image."""

    schedule_data = None
    error_log = []

    # ── Try Anthropic vision ──────────────────────────────────────────────────
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key and key.startswith("sk-ant"):
        try:
            import anthropic  # noqa: PLC0415
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                  "media_type": media_type,
                                                  "data": img_b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            raw = resp.content[0].text.strip()
            schedule_data = _parse_schedule_json(raw)
        except Exception as e:
            error_log.append(f"Anthropic: {e}")

    # ── Try Groq vision ───────────────────────────────────────────────────────
    if not schedule_data:
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            try:
                from openai import OpenAI  # noqa: PLC0415
                client = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
                resp = client.chat.completions.create(
                    model="llama-3.2-11b-vision-preview",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                        {"type": "text", "text": prompt},
                    ]}],
                )
                raw = resp.choices[0].message.content.strip()
                schedule_data = _parse_schedule_json(raw)
            except Exception as e:
                error_log.append(f"Groq: {e}")

    # ── Try Ollama vision (llava) ─────────────────────────────────────────────
    if not schedule_data:
        try:
            from openai import OpenAI  # noqa: PLC0415
            client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
            resp = client.chat.completions.create(
                model="llava",
                max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            raw = resp.choices[0].message.content.strip()
            schedule_data = _parse_schedule_json(raw)
        except Exception as e:
            error_log.append(f"Ollama/llava: {e}")

    if not schedule_data:
        return jsonify({
            "error": "Không thể đọc thời khóa biểu. Hãy thử ảnh rõ hơn.",
            "details": error_log,
        }), 422

    # ── Save to schedule.json ─────────────────────────────────────────────────
    FILES_FOLDER.mkdir(parents=True, exist_ok=True)
    out = FILES_FOLDER / "schedule.json"
    out.write_text(json.dumps(schedule_data, ensure_ascii=False, indent=2), encoding="utf-8")

    day_names = {"thu_2":"Thứ Hai","thu_3":"Thứ Ba","thu_4":"Thứ Tư",
                 "thu_5":"Thứ Năm","thu_6":"Thứ Sáu","thu_7":"Thứ Bảy","chu_nhat":"Chủ Nhật"}
    summary_lines = []
    for k, v in schedule_data.items():
        if v:
            summary_lines.append(f"{day_names.get(k, k)}: {', '.join(v)}")

    return jsonify({
        "reply": f"Đã đọc và lưu thời khóa biểu. {len(summary_lines)} ngày có lịch học.",
        "schedule": schedule_data,
        "summary": summary_lines,
    })


def _parse_schedule_json(raw: str) -> dict | None:
    """Extract and validate JSON from AI response."""
    # Strip markdown fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw.strip())
        valid_keys = {"thu_2","thu_3","thu_4","thu_5","thu_6","thu_7","chu_nhat"}
        if isinstance(data, dict) and any(k in valid_keys for k in data):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


if __name__ == "__main__":
    print("[app] Starting at http://localhost:5000")
    app.run(debug=False, port=5000)
