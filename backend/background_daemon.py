"""JARVIS V6 Always-On Background Daemon & Window Popper.
Runs silently in the background on Windows:
1. Listens for 'Jarvis wake up', 'Wake up Jarvis', 'Hey Jarvis', or Kurdish 'جاڕڤس هەڵسە'.
2. Listens for global hotkey Ctrl+Alt+J from anywhere in Windows.
3. Automatically wakes display, pops the JARVIS Command Center window to the front,
   and triggers the fullscreen Iron Man Holographic Intro sequence!
"""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

IS_WINDOWS = platform.system().lower() == "windows"
DASHBOARD_URL = "http://127.0.0.1:8000"
WAKE_API_URL = "http://127.0.0.1:8000/api/wake_trigger"


def bring_jarvis_window_to_front(open_if_missing: bool = True) -> str:
    """Restores, maximizes, and forces the JARVIS browser window to the foreground on Windows."""
    if not IS_WINDOWS:
        if open_if_missing:
            webbrowser.open(DASHBOARD_URL)
        return "Opened browser."

    # 1. Wake monitor if sleeping
    try:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, -1)
        ctypes.windll.user32.mouse_event(0x0001, 1, 1, 0, 0)
        ctypes.windll.user32.mouse_event(0x0001, -1, -1, 0, 0)
    except Exception:
        pass

    # 2. Search for open JARVIS Command Center window via win32gui
    found_hwnd = None
    try:
        import win32con
        import win32gui

        def _enum_cb(hwnd: int, _: object) -> None:
            nonlocal found_hwnd
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd) or ""
                if "jarvis command center" in title.lower() or "127.0.0.1:8000" in title.lower():
                    found_hwnd = hwnd

        win32gui.EnumWindows(_enum_cb, None)
        if found_hwnd:
            # Bypass Windows foreground lock by simulating Alt key press/release
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            win32gui.ShowWindow(found_hwnd, win32con.SW_RESTORE)
            win32gui.ShowWindow(found_hwnd, win32con.SW_MAXIMIZE)
            try:
                win32gui.SetForegroundWindow(found_hwnd)
            except Exception:
                pass
            ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)
            return "Restored and focused JARVIS Command Center window."
    except Exception:
        pass

    # 3. Fallback: PowerShell WScript.Shell AppActivate
    try:
        ps_cmd = (
            "$wshell = New-Object -ComObject wscript.shell; "
            "if ($wshell.AppActivate('JARVIS Command Center')) { exit 0 } else { exit 1 }"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            timeout=3,
            creationflags=0x08000000,
        )
        if r.returncode == 0:
            return "Activated JARVIS window via AppActivate."
    except Exception:
        pass

    # 4. If no JARVIS window is open, launch Edge/Chrome in borderless app/autoplay mode
    if open_if_missing:
        try:
            for browser in ("msedge", "chrome"):
                res = subprocess.run(["where", browser], capture_output=True, text=True, creationflags=0x08000000)
                if res.returncode == 0 and res.stdout.strip():
                    exe = res.stdout.strip().splitlines()[0].strip()
                    subprocess.Popen([
                        exe,
                        "--autoplay-policy=no-user-gesture-required",
                        "--new-window",
                        "--start-maximized",
                        DASHBOARD_URL,
                    ])
                    return f"Launched {browser} with JARVIS HUD."
        except Exception:
            pass
        webbrowser.open(DASHBOARD_URL)
        return "Opened JARVIS HUD in default browser."

    return "Window check completed."


def notify_backend_wake(source: str = "background_mic") -> None:
    """Triggers the wake sequence on the FastAPI backend and pops the window to the front."""
    bring_jarvis_window_to_front(open_if_missing=True)
    try:
        data = f'{{"source": "{source}"}}'.encode("utf-8")
        req = urllib.request.Request(WAKE_API_URL, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2.0)
    except Exception:
        pass


def _hotkey_listener_loop() -> None:
    """Registers global Windows hotkey Ctrl+Alt+J (0x4A) to wake JARVIS anytime."""
    if not IS_WINDOWS:
        return
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        VK_J = 0x4A
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, VK_J):
            return
        msg = wintypes.MSG()
        while user32.GetMessageA(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                notify_backend_wake(source="hotkey_ctrl_alt_j")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageA(ctypes.byref(msg))
    except Exception:
        pass


def _mic_wake_listener_loop() -> None:
    """Optional standalone Python SpeechRecognition background loop if SpeechRecognition + PyAudio are installed."""
    try:
        import speech_recognition as sr
    except ImportError:
        return

    WAKE_WORDS = (
        "wake up", "jarvis", "hey jarvis", "hi jarvis", "boot up",
        "هەڵسە", "هەستە", "بەخەبەر", "جاڕڤس", "جارڤیس", "استيقظ",
    )
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.energy_threshold = 280
    recognizer.pause_threshold = 0.6

    try:
        mic = sr.Microphone()
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
    except Exception:
        return

    while True:
        try:
            with mic as source:
                audio = recognizer.listen(source, timeout=3.5, phrase_time_limit=4.5)
            transcript = recognizer.recognize_google(audio).lower()
            if any(w in transcript for w in WAKE_WORDS):
                notify_backend_wake(source="python_background_mic")
                time.sleep(3.0)
        except Exception:
            time.sleep(0.4)


def start_background_watchers() -> None:
    """Starts the global hotkey listener and Python mic listener in background daemon threads."""
    threading.Thread(target=_hotkey_listener_loop, daemon=True, name="JarvisHotkeyDaemon").start()
    threading.Thread(target=_mic_wake_listener_loop, daemon=True, name="JarvisMicDaemon").start()


if __name__ == "__main__":
    print("[JARVIS V6 Background Daemon]: Active. Listening for 'Jarvis wake up' & Ctrl+Alt+J...")
    threading.Thread(target=_hotkey_listener_loop, daemon=True).start()
    _mic_wake_listener_loop()
    while True:
        time.sleep(10)
