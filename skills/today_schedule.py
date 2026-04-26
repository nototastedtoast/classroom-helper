"""
ZeroClaw skill: today_schedule
Reads C:/ClassroomFiles/schedule.txt or schedule.json and returns
today's schedule as a plain spoken Vietnamese string.

schedule.txt format (plain, one topic per line under a day header):
    Thứ Hai:
    Toán đại số chương 3
    Vật lý sóng âm

    Thứ Ba:
    ...

schedule.json format:
    {
      "thu_2": ["Toán đại số chương 3", "Vật lý sóng âm"],
      "thu_3": ["..."],
      ...
    }
    Day keys: thu_2, thu_3, thu_4, thu_5, thu_6, thu_7, chu_nhat
"""
import sys
import io
import json
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_FOLDER = Path("C:/ClassroomFiles")
_SCHEDULE_TXT  = _FOLDER / "schedule.txt"
_SCHEDULE_JSON = _FOLDER / "schedule.json"

# Vietnamese weekday names (Monday=0 … Sunday=6)
_VI_DAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
_JSON_KEYS = ["thu_2", "thu_3", "thu_4", "thu_5", "thu_6", "thu_7", "chu_nhat"]


def _today_vi() -> tuple[str, str]:
    """Return (vi_day_name, json_key) for today."""
    wd = datetime.now().weekday()  # 0=Mon
    return _VI_DAYS[wd], _JSON_KEYS[wd]


def _read_json() -> str | None:
    try:
        data = json.loads(_SCHEDULE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    _, key = _today_vi()
    topics = data.get(key) or data.get(key.replace("_", " ")) or []
    if not topics:
        return None
    vi_day, _ = _today_vi()
    joined = ", ".join(str(t) for t in topics)
    return f"{vi_day} hôm nay có các nội dung sau: {joined}."


def _read_txt() -> str | None:
    try:
        text = _SCHEDULE_TXT.read_text(encoding="utf-8")
    except Exception:
        return None

    vi_day, _ = _today_vi()
    lines = text.splitlines()
    in_section = False
    topics: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_section:
                break
            continue
        # header detection: line contains the day name (case-insensitive)
        if vi_day.lower() in stripped.lower() or stripped.rstrip(":").lower() == vi_day.lower():
            in_section = True
            continue
        # stop at next day header
        if in_section and stripped.endswith(":") and any(d.lower() in stripped.lower() for d in _VI_DAYS):
            break
        if in_section:
            topics.append(stripped)

    if not topics:
        return None
    joined = ", ".join(topics)
    return f"{vi_day} hôm nay có các nội dung sau: {joined}."


def run() -> str:
    vi_day, _ = _today_vi()

    if not _FOLDER.exists():
        return (
            f"Thư mục ClassroomFiles chưa tồn tại. "
            "Vui lòng tạo thư mục C:\\ClassroomFiles và thêm file schedule.txt."
        )

    result = _read_json() or _read_txt()

    if result:
        return result

    if not _SCHEDULE_TXT.exists() and not _SCHEDULE_JSON.exists():
        return (
            "Chưa có lịch học hôm nay. "
            "Bạn có thể tạo file schedule.txt trong thư mục ClassroomFiles."
        )

    return (
        f"Không tìm thấy lịch cho {vi_day}. "
        "Hãy kiểm tra lại file schedule.txt và đảm bảo có mục cho ngày hôm nay."
    )


if __name__ == "__main__":
    print(run())
