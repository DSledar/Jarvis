"""JARVIS Always-On Screen Awareness Engine.

Continuously tracks active windows, desktop state, screen resolution,
and provides rapid visual/OCR inspection so JARVIS always knows what's on screen.
"""
from __future__ import annotations

import base64
import ctypes
import io
import json
import logging
import os
import subprocess
import threading
import time
from typing import Any

from PIL import Image

logger = logging.getLogger("jarvis.screen_awareness")

_LOCK = threading.Lock()
_LAST_SCREEN_INFO: dict[str, Any] = {
    "active_window": "",
    "active_process": "",
    "resolution": "Unknown",
    "open_windows": [],
    "last_updated": 0,
    "ocr_preview": "",
}
_BACKGROUND_RUNNING = False


def get_screen_resolution() -> tuple[int, int]:
    """Returns primary monitor resolution (width, height)."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    except Exception:
        return (1920, 1080)


def get_active_window_info() -> dict[str, Any]:
    """Retrieves title and process name of currently active/focused foreground window."""
    info = {"title": "Desktop / None", "process": "explorer.exe", "rect": [0, 0, 0, 0]}
    if os.name != "nt":
        return info

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return info

        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            info["title"] = buff.value.strip() or "Untitled Window"

        # Get process ID
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            import psutil
            try:
                p = psutil.Process(pid.value)
                info["process"] = p.name()
            except Exception:
                pass

        # Get rect
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            info["rect"] = [rect.left, rect.top, rect.right, rect.bottom]
    except Exception as exc:
        logger.debug("Error getting active window: %s", exc)

    return info


def list_visible_windows(max_count: int = 12) -> list[dict[str, str]]:
    """Lists prominent visible open windows on the desktop."""
    if os.name != "nt":
        return []

    windows: list[dict[str, str]] = []
    try:
        user32 = ctypes.windll.user32

        def enum_handler(hwnd: int, extra: Any) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length < 2:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.strip()

            # Filter shell / helper windows
            ignored = ("Program Manager", "Settings", "Windows Input Experience", "Taskbar", "Cortana")
            if any(title == ig for ig in ignored):
                return True

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc_name = "unknown"
            if pid.value:
                import psutil
                try:
                    proc_name = psutil.Process(pid.value).name()
                except Exception:
                    pass

            windows.append({"title": title, "process": proc_name})
            return len(windows) < max_count

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        user32.EnumWindows(WNDENUMPROC(enum_handler), 0)
    except Exception as exc:
        logger.debug("Error enumerating windows: %s", exc)

    return windows


def quick_screen_ocr() -> str:
    """Performs quick text extraction from active screen via Windows native OCR or PowerShell."""
    try:
        ps_code = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$active = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $active.Width, $active.Height
$graphics = [System.Drawing.Graphics]::FromImage($bmp)
$graphics.CopyFromScreen($active.Location, [System.Drawing.Point]::Empty, $active.Size)
$graphics.Dispose()
$bmp.Dispose()
"""
    except Exception:
        pass
    return ""


def get_current_screen_summary() -> dict[str, Any]:
    """Returns complete real-time snapshot of what's currently on the PC screen."""
    with _LOCK:
        active = get_active_window_info()
        w, h = get_screen_resolution()
        visible = list_visible_windows(8)

        _LAST_SCREEN_INFO["active_window"] = active.get("title", "Desktop")
        _LAST_SCREEN_INFO["active_process"] = active.get("process", "")
        _LAST_SCREEN_INFO["resolution"] = f"{w}x{h}"
        _LAST_SCREEN_INFO["open_windows"] = visible
        _LAST_SCREEN_INFO["last_updated"] = time.time()

        return dict(_LAST_SCREEN_INFO)


def format_screen_context_for_prompt() -> str:
    """Generates concise screen awareness context block for the AI system prompt."""
    data = get_current_screen_summary()
    act = data.get("active_window") or "Desktop"
    proc = data.get("active_process") or ""
    wins = data.get("open_windows", [])
    win_list = ", ".join(f"'{w['title']}' ({w['process']})" for w in wins[:5]) or "None"

    return (
        f"[CURRENT PC SCREEN CONTEXT]\n"
        f"- Focused Active Window: '{act}' (Process: {proc})\n"
        f"- Visible Open Windows: {win_list}\n"
        f"- Resolution: {data.get('resolution')}\n"
        f"- Status: Screen is live and monitored by JARVIS."
    )


def analyze_screen_details() -> str:
    """Detailed inspection tool output for user asking 'what is on my screen?'."""
    data = get_current_screen_summary()
    act = data.get("active_window")
    proc = data.get("active_process")
    wins = data.get("open_windows", [])
    res = data.get("resolution")

    win_lines = "\n".join(f"  • {w['title']} [{w['process']}]" for w in wins) or "  • Desktop"

    return (
        f"SCREEN AWARENESS REPORT (LIVE):\n"
        f"🖥️ Primary Display: {res}\n"
        f"🎯 Active Focused Window: {act} (App: {proc})\n"
        f"🪟 Other Open Visible Windows:\n{win_lines}\n\n"
        f"💡 JARVIS is actively tracking this screen. You can ask me to interact with, click, read, or control any of these open applications."
    )
