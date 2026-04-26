"""
ZeroClaw skill: recap_lesson
Loads the last session row from SQLite and returns a context string.
ZeroClaw / main_shim passes this to the AI to generate a spoken recap.
After recap is delivered, call with --save-start to record new session start.
"""
import sys
import io
import sqlite3
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from datetime import datetime, timedelta
from pathlib import Path

_DB_PATH     = Path(__file__).parent.parent / "classroom.db"
_FILES_FOLDER = Path("C:/ClassroomFiles")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    summary      TEXT,
    last_file    TEXT,
    checkpoint   TEXT,
    started_at   TEXT
);
"""


def _ensure_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def _load_last_session(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT date, summary, last_file, checkpoint FROM sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {"date": row[0], "summary": row[1], "last_file": row[2], "checkpoint": row[3]}


def _save_session_start(conn: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessions (date, started_at) VALUES (?, ?)",
        (datetime.now().strftime("%Y-%m-%d"), now),
    )
    conn.commit()


def _recent_files(days: int = 7) -> list[str]:
    """Return names of files in ClassroomFiles modified within the last N days."""
    if not _FILES_FOLDER.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for p in _FILES_FOLDER.iterdir():
        if p.is_file():
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime >= cutoff:
                recent.append(p.name)
    return sorted(recent)


def run(save_start: bool = False) -> str:
    conn = _ensure_db()

    if save_start:
        _save_session_start(conn)
        conn.close()
        return "session_start_saved"

    last = _load_last_session(conn)
    conn.close()

    recent = _recent_files(days=7)

    if last is None:
        return json.dumps({
            "has_session": False,
            "message": "Chưa có buổi học nào được lưu. Đây là buổi đầu tiên.",
            "recent_files": recent,
        }, ensure_ascii=False)

    return json.dumps({
        "has_session": True,
        "date": last["date"],
        "summary": last["summary"] or "",
        "last_file": last["last_file"] or "",
        "checkpoint": last["checkpoint"] or "",
        "recent_files": recent,
        "instruction": (
            "Generate a calm, spoken 3-sentence Vietnamese recap of this session. "
            "No markdown, no lists, plain sentences only. "
            "Mention the date, main topic, and where to continue. "
            "If recent_files is non-empty, mention the most relevant file name naturally."
        ),
    }, ensure_ascii=False)


if __name__ == "__main__":
    save = "--save-start" in sys.argv
    print(run(save_start=save))
