"""Phone Control & Hardware Suite:
- ADB phone unlock, commands, and screen capture
- Wake-on-LAN (WoL) PC wake packet generation and network discovery
- Real-time PC screen capture and mobile screen receiving
- Sleep / Power control for PC from phone
"""
from __future__ import annotations

import base64
import io
import os
import platform
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SCREEN_DIR = DATA_DIR / "screen_captures"
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- ADB Discovery & Execution
_ADB_PATH: str | None = None


def find_adb() -> str | None:
    """Locates adb.exe on Windows or Unix."""
    global _ADB_PATH
    if _ADB_PATH and Path(_ADB_PATH).is_file():
        return _ADB_PATH

    # 1. System PATH
    try:
        res = subprocess.run(["where", "adb"] if platform.system().lower() == "windows" else ["which", "adb"],
                             capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            candidate = res.stdout.strip().splitlines()[0].strip()
            if Path(candidate).is_file():
                _ADB_PATH = candidate
                return candidate
    except Exception:
        pass

    # 2. Common Android SDK paths
    local_app_data = os.getenv("LOCALAPPDATA", "")
    user_home = Path.home()
    common_spots = [
        Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        user_home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path("C:/Program Files/platform-tools/adb.exe"),
        Path("C:/platform-tools/adb.exe"),
        Path("C:/adb/adb.exe"),
        user_home / "platform-tools" / "adb.exe",
        ROOT / "tools" / "adb.exe",
    ]
    for p in common_spots:
        if p.is_file():
            _ADB_PATH = str(p)
            return _ADB_PATH

    return None


def get_adb_devices() -> list[dict[str, str]]:
    adb = find_adb()
    if not adb:
        return []
    try:
        res = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        devices = []
        for line in res.stdout.strip().splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append({"serial": parts[0], "status": parts[1]})
        return devices
    except Exception:
        return []


def unlock_phone(pin: str = "", device_serial: str = "") -> dict[str, Any]:
    """Unlocks Android phone via ADB (if connected) or triggers companion web unlock."""
    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []

    if adb and adb_devs:
        target = device_serial or adb_devs[0]["serial"]
        cmd_prefix = [adb, "-s", target] if target else [adb]
        try:
            # 1. Wake screen (KEYCODE_POWER or KEYCODE_WAKEUP)
            subprocess.run([*cmd_prefix, "shell", "input", "keyevent", "224"], capture_output=True, timeout=5)
            time.sleep(0.4)

            # 2. Dismiss keyguard / swipe up
            subprocess.run([*cmd_prefix, "shell", "input", "swipe", "500", "1600", "500", "400", "300"],
                           capture_output=True, timeout=5)
            time.sleep(0.5)

            # 3. Enter PIN or password if provided
            if pin:
                clean_pin = re.sub(r"[^0-9a-zA-Z]", "", pin)
                subprocess.run([*cmd_prefix, "shell", "input", "text", clean_pin], capture_output=True, timeout=5)
                time.sleep(0.3)
                # Press ENTER
                subprocess.run([*cmd_prefix, "shell", "input", "keyevent", "66"], capture_output=True, timeout=5)

            return {
                "ok": True,
                "method": "adb",
                "device": target,
                "message": f"Sent unlock sequence to Android device {target} (Wake + Swipe + PIN).",
            }
        except Exception as exc:
            return {"ok": False, "method": "adb", "error": str(exc)}

    # A web page cannot dismiss Android's secure lock screen or inject the device PIN.
    return {"ok": False, "method": "companion_web", "error": "Android lock-screen control requires ADB. Connect by USB debugging or pair Wireless debugging in the Phone Control panel; the browser companion cannot unlock the operating system."}


def send_phone_key(key: str, device_serial: str = "") -> dict[str, Any]:
    """Sends navigation or hardware key command to phone via ADB or Companion Web."""
    key = str(key).strip().lower()
    key_map = {
        "home": "3",
        "back": "4",
        "power": "26",
        "wake": "224",
        "volup": "24",
        "volume_up": "24",
        "voldown": "25",
        "volume_down": "25",
        "menu": "187",
        "apps": "187",
        "enter": "66",
    }

    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []

    if adb and adb_devs:
        target = device_serial or adb_devs[0]["serial"]
        cmd_prefix = [adb, "-s", target] if target else [adb]
        try:
            if key in ("swipe_up", "swipe"):
                subprocess.run([*cmd_prefix, "shell", "input", "swipe", "500", "1600", "500", "400", "300"],
                               capture_output=True, timeout=5)
                return {"ok": True, "method": "adb", "message": "Dispatched swipe-up gesture via ADB"}
            elif key in key_map:
                code = key_map[key]
                subprocess.run([*cmd_prefix, "shell", "input", "keyevent", code],
                               capture_output=True, timeout=5)
                return {"ok": True, "method": "adb", "message": f"Sent keyevent {code} ({key}) via ADB"}
            else:
                return {"ok": False, "error": f"Unknown key event: {key}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # A mobile browser cannot send Android system keyevents; only ADB can do that.
    return {"ok": False, "method": "companion_web", "error": "Android system buttons need ADB. Connect by USB debugging or use the Wireless debugging pairing controls in JARVIS. The browser companion only supports web-page features such as flashlight requests, vibration, and speech."}


def request_phone_screenshot() -> dict[str, Any]:
    """Triggers phone screenshot capture via ADB or requests companion upload."""
    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []
    if adb and adb_devs:
        path = capture_phone_screenshot()
        if path:
            return {"ok": True, "path": path, "method": "adb", "message": "Captured ADB screenshot."}
    from . import devices
    devices.queue_phone_event("snap_photo", {"timestamp": time.time(), "message": "JARVIS requested camera/screen capture."})
    return {"ok": True, "method": "companion_web", "message": "Requested capture from connected phone companion."}


def capture_phone_screenshot() -> str:
    """Takes a screenshot of the connected phone via ADB or retrieves latest upload."""
    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []
    if adb and adb_devs:
        target = adb_devs[0]["serial"]
        out_file = SCREEN_DIR / f"phone_{int(time.time())}.png"
        try:
            res = subprocess.run([adb, "-s", target, "exec-out", "screencap", "-p"],
                                 capture_output=True, timeout=8)
            if res.returncode == 0 and len(res.stdout) > 1000:
                out_file.write_bytes(res.stdout)
                return str(out_file)
        except Exception:
            pass

    # Check for recent uploaded phone screens
    recent = sorted(SCREEN_DIR.glob("phone_upload_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if recent:
        return str(recent[0])
    return ""


# --------------------------------------------------------------------------- Phone Flashlight, Vibration, and Audio
_PHONE_TORCH_STATE: bool = False


def set_phone_torch(on: bool) -> dict[str, Any]:
    """Toggles phone flashlight / torch via ADB or push event to connected companion."""
    global _PHONE_TORCH_STATE
    _PHONE_TORCH_STATE = bool(on)

    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []
    adb_success = False
    if adb and adb_devs:
        target = adb_devs[0]["serial"]
        try:
            arg = "1" if on else "0"
            subprocess.run([adb, "-s", target, "shell", "cmd", "flashlight", arg],
                           capture_output=True, timeout=3)
            adb_success = True
        except Exception:
            pass

    from . import devices
    devices.queue_phone_event("torch", {
        "enabled": bool(on),
        "timestamp": time.time(),
        "message": f"Flashlight turned {'ON' if on else 'OFF'} by JARVIS.",
    })

    return {
        "ok": True,
        "state": bool(on),
        "adb": adb_success,
        "message": f"Phone flashlight turned {'ON' if on else 'OFF'}.",
    }


def get_phone_torch_state() -> bool:
    return _PHONE_TORCH_STATE


def vibrate_phone(duration_ms: int = 500) -> dict[str, Any]:
    """Requests phone to vibrate for specified duration in ms."""
    from . import devices
    devices.queue_phone_event("vibrate", {
        "duration": duration_ms,
        "pattern": [duration_ms, 150, duration_ms] if duration_ms > 400 else [duration_ms],
        "message": f"Vibrated for {duration_ms}ms",
    })
    return {"ok": True, "message": f"Vibration signal ({duration_ms}ms) sent to phone."}


def speak_on_phone(text: str) -> dict[str, Any]:
    """Requests phone to announce text using its speaker / TTS."""
    from . import devices
    devices.queue_phone_event("speak", {
        "text": text,
        "message": f"Speak requested: {text}",
    })
    return {"ok": True, "message": f"Requested phone to speak: '{text}'."}


# --------------------------------------------------------------------------- Wake-on-LAN (WoL) & Sleep
def get_mac_address() -> str:
    """Retrieves physical MAC address of active PC network adapter."""
    # 1. On Windows, get physical MAC of the Up adapter from PowerShell
    if platform.system().lower() == "windows":
        try:
            ps_cmd = 'Get-NetAdapter | Where-Object Status -eq "Up" | Select-Object -ExpandProperty MacAddress'
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                                 capture_output=True, text=True, timeout=4)
            for line in res.stdout.strip().splitlines():
                clean = re.sub(r"[^0-9a-fA-F]", "", line.strip())
                if len(clean) == 12:
                    return ":".join(clean[i:i+2].upper() for i in range(0, 12, 2))
        except Exception:
            pass

    # 2. Inspect psutil interfaces
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            has_private_ip = False
            mac = ""
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    has_private_ip = True
                if hasattr(psutil, "AF_LINK") and a.family == psutil.AF_LINK:
                    mac = a.address
            if has_private_ip and mac and len(mac.replace("-", "").replace(":", "")) == 12:
                clean = re.sub(r"[^0-9a-fA-F]", "", mac)
                return ":".join(clean[i:i+2].upper() for i in range(0, 12, 2))
    except Exception:
        pass

    # 3. Known physical Realtek Wi-Fi hardware MAC fallback
    return "9C:C7:D3:36:72:C6"


def get_wol_info() -> dict[str, Any]:
    """Returns PC network details needed for phone to send Wake-on-LAN magic packet."""
    mac = get_mac_address()
    from . import devices
    ip = devices.get_local_ip()

    # Determine broadcast IP
    parts = ip.split(".")
    broadcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255" if len(parts) == 4 else "255.255.255.255"

    return {
        "pc_name": socket.gethostname(),
        "mac_address": mac,
        "local_ip": ip,
        "broadcast_ip": broadcast,
        "port": 9,
        "instructions": (
            "To wake this PC from your phone when it is asleep:\n"
            f"1. Make sure Wake-on-LAN is enabled on Realtek 8852BE WiFi (run enable_laptop_wol.bat as Admin).\n"
            f"2. Your PC MAC address is: {mac}\n"
            f"3. Send WoL Magic Packet to {broadcast} port 9.\n"
            "4. When PC is asleep, use the Android WoL 1-tap intent or a free WoL app."
        ),
    }


def send_wake_on_lan(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> bool:
    """Sends a magic packet to wake a target PC on the local network."""
    clean_mac = re.sub(r"[^0-9a-fA-F]", "", mac_address)
    if len(clean_mac) != 12:
        raise ValueError(f"Invalid MAC address: {mac_address} (must be 12 hex characters)")

    mac_bytes = bytes.fromhex(clean_mac)
    magic_packet = b"\xff" * 6 + mac_bytes * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet, (broadcast_ip, port))
    return True


_PC_SLEEP_ACTIVE = False
_STANDBY_FRAME_CACHE = None


def wake_pc_display() -> str:
    """Wakes the PC monitor from Modern Standby (S0) or screen-off idle."""
    global _PC_SLEEP_ACTIVE
    _PC_SLEEP_ACTIVE = False
    if platform.system().lower() == "windows":
        try:
            import ctypes
            # SC_MONITORPOWER -1 = display power ON
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, -1)
            # Send subtle mouse nudge to signal user presence and resume display
            ctypes.windll.user32.mouse_event(0x0001, 1, 1, 0, 0)
            ctypes.windll.user32.mouse_event(0x0001, -1, -1, 0, 0)
        except Exception:
            pass
    return "PC display woken from standby."


def sleep_pc() -> str:
    """Puts Windows PC to real sleep mode (supporting both Modern Standby S0 and Legacy S3)."""
    if platform.system().lower() != "windows":
        return "Sleep command is only supported on Windows."

    global _PC_SLEEP_ACTIVE
    _PC_SLEEP_ACTIVE = True

    def _execute_sleep() -> None:
        import ctypes
        time.sleep(0.5)  # Allow HTTP response to reach the phone companion before sleep begins

        # 1. Lock the workstation so PC is fully password-protected while sleeping
        try:
            ctypes.windll.user32.LockWorkStation()
        except Exception:
            pass

        # 2. Give Windows lock screen 1.2s to fully initialize before cutting monitor power
        time.sleep(1.2)

        # 3. For systems supporting traditional ACPI S3 sleep
        try:
            ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
        except Exception:
            pass

        # 4. For Modern Standby (S0 Low Power Idle) laptops, turning off monitor puts Windows into S0 sleep
        try:
            # HWND_BROADCAST = 0xFFFF, WM_SYSCOMMAND = 0x0112, SC_MONITORPOWER = 0xF170, 2 = Monitor Off
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception:
            pass

        # 5. Fallback via background PowerShell to reinforce low-power standby state
        try:
            subprocess.Popen([
                "powershell.exe", "-WindowStyle", "Hidden", "-Command",
                "Start-Sleep -Milliseconds 300; (Add-Type '[DllImport(\"user32.dll\")]public static extern int SendMessage(int h,int m,int w,int l);' -Name f -Namespace n -PassThru)::SendMessage(0xffff,0x112,0xf170,2)"
            ], creationflags=subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0)
        except Exception:
            pass

    threading.Thread(target=_execute_sleep, daemon=True).start()
    return "PC is entering sleep mode now. Tap screen on phone or use Wake-on-LAN to wake it up."


def _get_standby_frame_bytes() -> bytes:
    """Generates a high-tech Stark standby placeholder frame to avoid waking the display during S0 sleep."""
    global _STANDBY_FRAME_CACHE
    if _STANDBY_FRAME_CACHE:
        return _STANDBY_FRAME_CACHE
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (960, 540), color=(3, 11, 17))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(20, 20), (940, 520)], outline=(19, 102, 119), width=2)
    draw.text((60, 160), "J.A.R.V.I.S. // PC IN MODERN STANDBY (SLEEP)", fill=(0, 229, 255))
    draw.text((60, 210), f"Host: {socket.gethostname()} | Status: Standby S0 Active (Low Power Idle)", fill=(226, 232, 240))
    draw.text((60, 260), "Monitor is powered off. Tap anywhere on mobile screen or send WoL to wake.", fill=(148, 163, 184))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    _STANDBY_FRAME_CACHE = buf.getvalue()
    return _STANDBY_FRAME_CACHE


def _draw_high_vis_cursor(img: Any, cx: int, cy: int) -> None:
    """Renders a prominent, high-visibility Stark holographic cursor that is clearly visible on mobile screens."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    # 1. Outer glowing cyan radar ring
    r = 15
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=(0, 229, 255), width=2)
    # 2. Center targeting dot
    draw.ellipse([(cx - 2, cy - 2), (cx + 2, cy + 2)], fill=(0, 229, 255))
    # 3. High-visibility arrow pointer scaled to 32px height for mobile screens
    arrow = [
        (cx, cy),
        (cx, cy + 30),
        (cx + 8, cy + 23),
        (cx + 15, cy + 37),
        (cx + 20, cy + 34),
        (cx + 13, cy + 21),
        (cx + 22, cy + 21)
    ]
    # Dark drop shadow for contrast against white backgrounds
    draw.polygon([(x + 2, y + 2) for x, y in arrow], fill=(0, 0, 0), outline=(0, 0, 0))
    # Main crisp white body with dark border
    draw.polygon(arrow, fill=(255, 255, 255), outline=(0, 0, 0))
    # Cyan Stark accent tip
    draw.polygon([(cx, cy), (cx, cy + 12), (cx + 6, cy + 10)], fill=(0, 229, 255))


# --------------------------------------------------------------------------- Real-Time PC Screen Capture
def capture_pc_screen_bytes(max_dim: int = 960, quality: int = 58) -> bytes:
    """Capture a compact JPEG quickly; use MSS first and Pillow as a portable fallback."""
    global _PC_SLEEP_ACTIVE
    if _PC_SLEEP_ACTIVE:
        return _get_standby_frame_bytes()

    from PIL import Image
    img = None
    # MSS is usually faster than repeated ImageGrab calls on Windows.
    try:
        import mss
        with mss.mss() as screen:
            monitor = screen.monitors[1] if len(screen.monitors) > 1 else screen.monitors[0]
            shot = screen.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        img = None
    if img is None:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
        except Exception:
            try:
                import pyautogui
                img = pyautogui.screenshot()
            except Exception:
                pass
    if img is None:
        from PIL import ImageDraw
        img = Image.new("RGB", (960, 540), color=(18, 24, 38))
        draw = ImageDraw.Draw(img)
        draw.text((60, 160), "JARVIS PC DISPLAY", fill=(56, 189, 248))
        draw.text((60, 210), f"Host: {socket.gethostname()} | Status: Screen Locked / Sleep Mode", fill=(226, 232, 240))
        draw.text((60, 260), "To view live desktop stream, unlock your PC or press Wake PC.", fill=(148, 163, 184))
        draw.rectangle([(50, 120), (910, 420)], outline=(45, 55, 72), width=2)

    # Determine mouse cursor position with DPI awareness
    mx = my = None
    sw = sh = None
    try:
        import pyautogui
        pos = pyautogui.position()
        if pos:
            mx, my = int(pos.x), int(pos.y)
        sw, sh = pyautogui.size()
    except Exception:
        pass
    if mx is None or my is None:
        try:
            import ctypes
            from ctypes import wintypes
            pt = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                mx, my = int(pt.x), int(pt.y)
        except Exception:
            pass

    orig_w, orig_h = img.size
    scale = min(1.0, max_dim / max(orig_w, orig_h)) if max(orig_w, orig_h) else 1.0
    if scale < 1.0:
        img = img.resize((max(1, int(orig_w * scale)), max(1, int(orig_h * scale))), Image.Resampling.BILINEAR)

    # Draw high-visibility cursor
    if mx is not None and my is not None:
        try:
            # Use logical screen dimensions to normalize against Windows display scaling
            ref_w = sw if (sw and sw > 0) else orig_w
            ref_h = sh if (sh and sh > 0) else orig_h
            nx = max(0.0, min(1.0, mx / ref_w))
            ny = max(0.0, min(1.0, my / ref_h))
            cx = int(nx * img.width)
            cy = int(ny * img.height)
            _draw_high_vis_cursor(img, cx, cy)
        except Exception:
            pass

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=max(35, min(80, int(quality))), optimize=False, progressive=False)
    return buf.getvalue()


def get_pc_screen_base64() -> str:
    """Returns base64 data URL for instant HTML image rendering."""
    raw = capture_pc_screen_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
