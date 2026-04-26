"""
ZeroClaw skill: slide_control
Detects slide intent from spoken command and advances the foreground
PowerPoint or PDF viewer accordingly.

Usage:
    python skills/slide_control.py "tiếp theo"
    python skills/slide_control.py "slide 5"
    python skills/slide_control.py "quay lại"
"""
import sys
import io
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------
_NEXT_WORDS  = {"tiếp theo", "next", "tiếp", "kế tiếp", "sang"}
_PREV_WORDS  = {"trước", "back", "quay lại", "trở lại", "lùi"}
_FIRST_WORDS = {"đầu tiên", "first", "slide đầu", "trang đầu"}


def _parse_intent(text: str) -> tuple[str, int | None]:
    """Return (action, slide_number).  action ∈ next|prev|first|goto|unknown."""
    lower = text.lower().strip()

    # "slide 5" / "trang 3" / "go to 7"
    m = re.search(r"(?:slide|trang|số|go to|đến)\s*(\d+)", lower)
    if m:
        return ("goto", int(m.group(1)))

    for w in _FIRST_WORDS:
        if w in lower:
            return ("first", None)
    for w in _NEXT_WORDS:
        if w in lower:
            return ("next", None)
    for w in _PREV_WORDS:
        if w in lower:
            return ("prev", None)

    return ("unknown", None)


# ---------------------------------------------------------------------------
# Foreground app detection
# ---------------------------------------------------------------------------
def _get_foreground_app() -> str:
    """Return 'powerpoint', 'pdf', or 'unknown'."""
    try:
        import win32gui  # noqa: PLC0415
        import win32process  # noqa: PLC0415
        import psutil  # noqa: PLC0415

        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = psutil.Process(pid).name().lower()

        if "powerpnt" in name or "powerpoint" in name:
            return "powerpoint"
        if any(x in name for x in ("acrobat", "pdf", "sumatra", "foxitreader", "okular")):
            return "pdf"
        # also check window title for PDF viewers that don't have obvious process names
        title = win32gui.GetWindowText(hwnd).lower()
        if ".pdf" in title or "pdf" in title:
            return "pdf"
        if "powerpoint" in title or ".pptx" in title or ".ppt" in title:
            return "powerpoint"
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# PowerPoint control via COM
# ---------------------------------------------------------------------------
def _control_powerpoint(action: str, slide_number: int | None) -> str:
    try:
        import win32com.client  # noqa: PLC0415
        ppt = win32com.client.GetActiveObject("PowerPoint.Application")
        show = ppt.SlideShowWindows(1)
        view = show.View
        current = view.CurrentShowPosition
        total   = show.Presentation.Slides.Count

        if action == "next":
            view.Next()
            return f"Slide tiếp theo"
        elif action == "prev":
            view.Previous()
            return "Slide trước"
        elif action == "first":
            view.GotoSlide(1)
            return "Quay về slide đầu tiên"
        elif action == "goto" and slide_number is not None:
            if 1 <= slide_number <= total:
                view.GotoSlide(slide_number)
                return f"Chuyển đến slide {slide_number}"
            return f"Slide {slide_number} không tồn tại. Bài có {total} slide."
    except Exception as e:
        # slideshow might not be running — try opening presentation mode
        if "not found" in str(e).lower() or "Invalid" in str(e):
            return "Không tìm thấy bài trình chiếu đang chạy. Hãy bắt đầu trình chiếu trước."
        return f"Lỗi điều khiển PowerPoint: {e}"
    return "Không thể điều khiển PowerPoint."


# ---------------------------------------------------------------------------
# PDF / generic viewer control via keyboard simulation
# ---------------------------------------------------------------------------
def _control_pdf(action: str, slide_number: int | None) -> str:
    try:
        import pyautogui  # noqa: PLC0415
        pyautogui.FAILSAFE = False

        if action == "next":
            pyautogui.press("right")
            return "Trang tiếp theo"
        elif action == "prev":
            pyautogui.press("left")
            return "Trang trước"
        elif action == "first":
            pyautogui.hotkey("ctrl", "home")
            return "Quay về trang đầu tiên"
        elif action == "goto" and slide_number is not None:
            # most PDF viewers: Ctrl+G or just type page number
            # Sumatra / Acrobat both support Ctrl+G
            pyautogui.hotkey("ctrl", "g")
            import time  # noqa: PLC0415
            time.sleep(0.3)
            pyautogui.typewrite(str(slide_number), interval=0.05)
            pyautogui.press("enter")
            return f"Chuyển đến trang {slide_number}"
    except Exception as e:
        return f"Lỗi điều khiển PDF: {e}"
    return "Không thể điều khiển trình xem PDF."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(command: str) -> str:
    action, slide_number = _parse_intent(command)

    if action == "unknown":
        return "Không hiểu lệnh điều hướng slide. Thử nói tiếp theo, trước, hoặc slide số mấy."

    app = _get_foreground_app()

    if app == "powerpoint":
        return _control_powerpoint(action, slide_number)
    elif app == "pdf":
        return _control_pdf(action, slide_number)
    else:
        # Try PowerPoint first (may be in background), then keyboard fallback
        try:
            import win32com.client  # noqa: PLC0415
            win32com.client.GetActiveObject("PowerPoint.Application")
            return _control_powerpoint(action, slide_number)
        except Exception:
            pass
        # Last resort: send arrow key and hope for the best
        try:
            import pyautogui  # noqa: PLC0415
            pyautogui.FAILSAFE = False
            key = "right" if action in ("next", "goto") else "left"
            if action == "goto" and slide_number:
                pyautogui.hotkey("ctrl", "g")
                import time; time.sleep(0.3)  # noqa: E702
                pyautogui.typewrite(str(slide_number), interval=0.05)
                pyautogui.press("enter")
                return f"Đã gửi lệnh đến trang {slide_number}"
            pyautogui.press(key)
            label = "tiếp theo" if action == "next" else "trước"
            return f"Đã gửi phím điều hướng {label}"
        except Exception:
            return "Không tìm thấy bài trình chiếu. Hãy mở PowerPoint hoặc file PDF trước."


if __name__ == "__main__":
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(run(cmd))
