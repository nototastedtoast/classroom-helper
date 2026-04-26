"""
ZeroClaw skill: open_file
Searches for a file by spoken name across common locations,
then opens it with the default application.
"""
import os
import sys
import io
from pathlib import Path
from difflib import get_close_matches

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Locations searched in order — first match wins
_SEARCH_ROOTS = [
    Path("C:/ClassroomFiles"),
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path("D:/"),
    Path("E:/"),
]

# Only open these extensions (safety: skip .exe, .bat, etc.)
_ALLOWED_EXT = {
    ".pdf", ".pptx", ".ppt", ".docx", ".doc",
    ".xlsx", ".xls", ".txt", ".md", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mp3", ".wav",
}


def _index(roots: list[Path], max_depth: int = 3) -> dict[str, Path]:
    """Return {lowercase_name: full_path} for all allowed files under roots."""
    index: dict[str, Path] = {}

    def _walk(path: Path, depth: int):
        if depth == 0 or not path.exists():
            return
        try:
            for p in path.iterdir():
                if p.is_file() and p.suffix.lower() in _ALLOWED_EXT:
                    index[p.stem.lower()] = p
                    index[p.name.lower()] = p
                elif p.is_dir() and not p.name.startswith("."):
                    _walk(p, depth - 1)
        except PermissionError:
            pass

    for root in roots:
        _walk(root, max_depth)
    return index


def run(spoken_name: str) -> str:
    if not spoken_name or not spoken_name.strip():
        return "Bạn muốn mở file nào? Hãy nói tên file."

    query = spoken_name.strip().lower()
    index = _index(_SEARCH_ROOTS)

    if not index:
        return "Không tìm thấy tài liệu nào trong các thư mục thông dụng."

    # Exact match first
    if query in index:
        return _open(index[query])

    # Fuzzy match
    matches = get_close_matches(query, list(index.keys()), n=3, cutoff=0.45)
    if matches:
        best = index[matches[0]]
        try:
            os.startfile(str(best))
            others = ""
            if len(matches) > 1:
                others = " Cũng tìm thấy: " + ", ".join(
                    index[m].stem for m in matches[1:]
                ) + "."
            return f"Đang mở {best.stem}.{others}"
        except Exception as e:
            return f"Tìm thấy {best.name} nhưng không thể mở: {e}"

    # Show what's available
    sample = sorted({p.stem for p in list(index.values())[:8]})
    return (
        f"Không tìm thấy file '{spoken_name}'. "
        f"Một số file có sẵn: {', '.join(sample)}."
    )


def _open(path: Path) -> str:
    try:
        os.startfile(str(path))
        return f"Đang mở {path.stem}"
    except Exception as e:
        return f"Không thể mở {path.name}: {e}"


if __name__ == "__main__":
    name = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(run(name))
