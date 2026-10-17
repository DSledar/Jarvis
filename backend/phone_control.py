"""Phone Control & Hardware Suite:
- ADB phone unlock, commands, and screen capture
- Wake-on-LAN (WoL) PC wake packet generation and network discovery
- Real-time PC screen capture and mobile screen receiving
- Sleep / Power control for PC from phone
"""
from __future__ import annotations

import base64
import io
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SAVED_PHONES_FILE = DATA_DIR / "saved_phones.json"
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
        ROOT / "tools" / "platform-tools" / "adb.exe",
        ROOT / "tools" / "adb.exe",
        Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        user_home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path("C:/Program Files/platform-tools/adb.exe"),
        Path("C:/platform-tools/adb.exe"),
        Path("C:/adb/adb.exe"),
        user_home / "platform-tools" / "adb.exe",
    ]
    for p in common_spots:
        if p.is_file():
            _ADB_PATH = str(p)
            return _ADB_PATH

    return None


def download_adb() -> dict[str, Any]:
    """Downloads official Google Android platform-tools (adb.exe) into tools/platform-tools."""
    import zipfile
    import urllib.request

    tools_dir = ROOT / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    exe_name = "adb.exe" if platform.system().lower() == "windows" else "adb"
    target_adb = tools_dir / "platform-tools" / exe_name
    if target_adb.is_file():
        global _ADB_PATH
        _ADB_PATH = str(target_adb)
        return {"ok": True, "path": str(target_adb), "message": "ADB is already installed."}

    url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    zip_path = tools_dir / "platform-tools.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Agent/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(zip_path, "wb") as out_f:
            out_f.write(resp.read())
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(tools_dir)
        try:
            zip_path.unlink()
        except Exception:
            pass
        if target_adb.is_file():
            _ADB_PATH = str(target_adb)
            return {"ok": True, "path": str(target_adb), "message": "Android ADB successfully downloaded and installed!"}
        return {"ok": False, "error": "Extracted archive but adb executable was not found."}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to download ADB: {exc}"}


# --------------------------------------------------------------------------- Native Interactive Phone Mirroring (scrcpy 60FPS)
_SCRCPY_PATH: str | None = None
_SCRCPY_PROC: subprocess.Popen | None = None
_SCRCPY_LOCK = threading.RLock()


def find_scrcpy() -> str | None:
    """Locates scrcpy executable on Windows or Unix."""
    global _SCRCPY_PATH
    if _SCRCPY_PATH and Path(_SCRCPY_PATH).is_file():
        return _SCRCPY_PATH

    import shutil
    candidate = shutil.which("scrcpy") or shutil.which("scrcpy.exe")
    if candidate and Path(candidate).is_file():
        _SCRCPY_PATH = candidate
        return candidate

    local_app_data = os.getenv("LOCALAPPDATA", "")
    user_home = Path.home()
    spots = [
        ROOT / "tools" / "scrcpy" / "scrcpy.exe",
        ROOT / "tools" / "scrcpy.exe",
        Path("C:/Program Files/scrcpy/scrcpy.exe"),
        Path("C:/scrcpy/scrcpy.exe"),
        user_home / "AppData" / "Local" / "Programs" / "scrcpy" / "scrcpy.exe",
    ]
    # Check WinGet package directories
    winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if winget_packages.is_dir():
        try:
            for p in winget_packages.glob("*scrcpy*/**/*.exe"):
                if p.name.lower() == "scrcpy.exe" and p.is_file():
                    _SCRCPY_PATH = str(p)
                    return _SCRCPY_PATH
        except Exception:
            pass

    for s in spots:
        if s.is_file():
            _SCRCPY_PATH = str(s)
            return _SCRCPY_PATH

    return None


def launch_scrcpy_mirror(serial: str = "", title: str = "", stay_awake: bool = True) -> dict[str, Any]:
    """Launches a native 60FPS high-speed interactive window on Windows via scrcpy."""
    global _SCRCPY_PROC
    scrcpy_bin = find_scrcpy()
    if not scrcpy_bin:
        return {
            "ok": False,
            "error": "scrcpy was not found on this system. Please install scrcpy or place scrcpy.exe in tools/scrcpy."
        }

    adb_bin = find_adb()
    connected = get_adb_devices()
    target_serial = serial.strip()

    if not target_serial:
        if connected:
            target_serial = connected[0].get("serial", "")
        else:
            # Check saved phones with Wi-Fi IP
            saved = load_saved_phones()
            devices_map = saved.get("devices", {})
            for s, info in devices_map.items():
                wifi_ip = info.get("wifi_ip", "").strip()
                if wifi_ip and adb_bin:
                    try:
                        subprocess.run([adb_bin, "connect", f"{wifi_ip}:5555"], capture_output=True, timeout=3)
                        target_serial = s
                        break
                    except Exception:
                        pass

    model_name = ""
    if target_serial:
        model_name = get_device_model(target_serial)

    win_title = title.strip() or f"JARVIS Mobile Interface - {model_name or target_serial or 'Live Phone'}"
    cmd = [scrcpy_bin, "--window-title", win_title]
    if stay_awake:
        cmd.append("--stay-awake")
    if target_serial:
        cmd.extend(["-s", target_serial])

    # Pass ADB in environment
    env = os.environ.copy()
    if adb_bin:
        env["ADB"] = adb_bin
        adb_dir = str(Path(adb_bin).parent)
        env["PATH"] = f"{adb_dir};{env.get('PATH', '')}"

    with _SCRCPY_LOCK:
        if _SCRCPY_PROC and _SCRCPY_PROC.poll() is None:
            return {
                "ok": True,
                "already_running": True,
                "pid": _SCRCPY_PROC.pid,
                "message": f"Interactive mobile screen window is already open ({win_title})."
            }

        try:
            _SCRCPY_PROC = subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            return {
                "ok": True,
                "pid": _SCRCPY_PROC.pid,
                "serial": target_serial,
                "model": model_name,
                "message": f"Interactive mobile interface opened smoothly on Windows ({win_title})."
            }
        except Exception as exc:
            return {"ok": False, "error": f"Failed to start scrcpy: {exc}"}


def stop_scrcpy_mirror() -> dict[str, Any]:
    """Closes the active scrcpy phone mirror window if running."""
    global _SCRCPY_PROC
    with _SCRCPY_LOCK:
        if _SCRCPY_PROC and _SCRCPY_PROC.poll() is None:
            try:
                _SCRCPY_PROC.terminate()
                _SCRCPY_PROC.wait(timeout=2)
            except Exception:
                try:
                    _SCRCPY_PROC.kill()
                except Exception:
                    pass
            _SCRCPY_PROC = None
            return {"ok": True, "message": "Closed interactive mobile screen."}
        _SCRCPY_PROC = None
        return {"ok": True, "message": "No active mobile screen was running."}


def get_scrcpy_status() -> dict[str, Any]:
    """Returns scrcpy availability and running status."""
    scrcpy_bin = find_scrcpy()
    is_running = False
    with _SCRCPY_LOCK:
        if _SCRCPY_PROC and _SCRCPY_PROC.poll() is None:
            is_running = True
    return {
        "available": bool(scrcpy_bin),
        "path": scrcpy_bin,
        "running": is_running,
        "pid": _SCRCPY_PROC.pid if is_running and _SCRCPY_PROC else None
    }



# --------------------------------------------------------------------------- Phone Memory & Saved Credentials
_SAVED_PHONES_LOCK = threading.RLock()


def load_saved_phones() -> dict[str, Any]:
    """Loads remembered phones and saved credentials from saved_phones.json."""
    with _SAVED_PHONES_LOCK:
        if SAVED_PHONES_FILE.is_file():
            try:
                data = json.loads(SAVED_PHONES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "devices" not in data:
                        data["devices"] = {}
                    return data
            except Exception:
                pass
        return {"devices": {}, "last_unlocked_pin": "", "default_serial": ""}


def save_phone_memory(
    serial: str,
    pin: str = "",
    model: str = "",
    connection_type: str = "usb",
    auto_unlock: bool = True,
) -> dict[str, Any]:
    """Saves or updates a remembered phone with its PIN, model name, and connection type."""
    if not serial:
        return {"ok": False, "error": "Device serial is required."}
    with _SAVED_PHONES_LOCK:
        data = load_saved_phones()
        clean_serial = serial.strip()
        devices = data.setdefault("devices", {})
        curr = devices.get(clean_serial, {})

        # If pin not provided, preserve existing pin
        saved_pin = pin.strip() if pin else curr.get("pin", "")
        saved_model = model.strip() if model else curr.get("model", "")
        if not saved_model:
            saved_model = get_device_model(clean_serial)

        devices[clean_serial] = {
            "serial": clean_serial,
            "model": saved_model,
            "pin": saved_pin,
            "has_pin": bool(saved_pin),
            "connection_type": connection_type or curr.get("connection_type", "usb"),
            "auto_unlock": bool(auto_unlock),
            "last_connected": time.time(),
        }
        if saved_pin:
            data["last_unlocked_pin"] = saved_pin
        data["default_serial"] = clean_serial

        try:
            SAVED_PHONES_FILE.parent.mkdir(parents=True, exist_ok=True)
            SAVED_PHONES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return {"ok": True, "device": devices[clean_serial]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def get_saved_pin_for_device(serial: str = "") -> str:
    """Retrieves saved PIN for given device serial, or first active connected phone, or fallback last PIN."""
    data = load_saved_phones()
    devices = data.get("devices", {})
    if serial and serial in devices:
        pin = devices[serial].get("pin", "").strip()
        if pin:
            return pin

    # Try currently connected ADB devices
    adb_devs = get_adb_devices()
    for d in adb_devs:
        s = d.get("serial", "")
        if s in devices and devices[s].get("pin", "").strip():
            return devices[s]["pin"].strip()

    # Fallback to last unlocked PIN
    return data.get("last_unlocked_pin", "").strip()


def get_saved_phones_list() -> list[dict[str, Any]]:
    """Returns list of remembered phones with masked PIN status for the UI."""
    data = load_saved_phones()
    result = []
    for s, info in data.get("devices", {}).items():
        pin = info.get("pin", "")
        result.append({
            "serial": s,
            "model": info.get("model") or "Android Device",
            "has_pin": bool(pin),
            "masked_pin": "••••" if pin else "",
            "pin_length": len(pin) if pin else 0,
            "connection_type": info.get("connection_type", "usb"),
            "auto_unlock": info.get("auto_unlock", True),
            "last_connected": info.get("last_connected", 0),
        })
    result.sort(key=lambda x: x["last_connected"], reverse=True)
    return result


def get_saved_phone_details(serial: str) -> dict[str, Any] | None:
    """Returns detailed phone configuration including editable PIN for the owner."""
    data = load_saved_phones()
    clean_serial = serial.strip()
    devices = data.get("devices", {})
    if clean_serial in devices:
        d = dict(devices[clean_serial])
        return d
    return None


def update_saved_phone(
    serial: str,
    model: str | None = None,
    pin: str | None = None,
    connection_type: str | None = None,
    auto_unlock: bool | None = None,
    new_serial: str | None = None,
) -> dict[str, Any]:
    """Updates existing saved phone's model, PIN, connection type, or serial."""
    clean_serial = serial.strip()
    if not clean_serial:
        return {"ok": False, "error": "Serial is required to update a phone."}

    with _SAVED_PHONES_LOCK:
        data = load_saved_phones()
        devices = data.setdefault("devices", {})

        if clean_serial not in devices:
            return {"ok": False, "error": f"Phone with serial '{clean_serial}' not found in saved list."}

        phone_entry = devices[clean_serial]

        if model is not None:
            phone_entry["model"] = model.strip() or phone_entry.get("model", "Android Device")
        if pin is not None:
            clean_pin = pin.strip()
            phone_entry["pin"] = clean_pin
            phone_entry["has_pin"] = bool(clean_pin)
            if clean_pin:
                data["last_unlocked_pin"] = clean_pin
        if connection_type is not None:
            valid_types = ("usb", "wifi", "cellular", "any")
            chosen = connection_type.strip().lower()
            phone_entry["connection_type"] = chosen if chosen in valid_types else "usb"
        if auto_unlock is not None:
            phone_entry["auto_unlock"] = bool(auto_unlock)

        # Handle serial rename if requested
        if new_serial and new_serial.strip() != clean_serial:
            target_serial = new_serial.strip()
            phone_entry["serial"] = target_serial
            devices[target_serial] = phone_entry
            del devices[clean_serial]
            clean_serial = target_serial

        phone_entry["last_connected"] = time.time()

        try:
            SAVED_PHONES_FILE.parent.mkdir(parents=True, exist_ok=True)
            SAVED_PHONES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return {"ok": True, "device": phone_entry}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def add_saved_phone(
    serial: str,
    model: str = "",
    pin: str = "",
    connection_type: str = "usb",
    auto_unlock: bool = True,
) -> dict[str, Any]:
    """Manually adds a new phone with specified model name, PIN, and connection type."""
    clean_serial = serial.strip()
    if not clean_serial:
        return {"ok": False, "error": "Phone Serial or IP address is required."}

    return save_phone_memory(
        serial=clean_serial,
        pin=pin,
        model=model or clean_serial,
        connection_type=connection_type,
        auto_unlock=auto_unlock,
    )


def delete_saved_phone(serial: str) -> bool:
    """Removes a phone from the remembered devices database."""
    with _SAVED_PHONES_LOCK:
        data = load_saved_phones()
        if serial in data.get("devices", {}):
            del data["devices"][serial]
            try:
                SAVED_PHONES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
                return True
            except Exception:
                pass
    return False


def get_device_model(serial: str) -> str:
    """Queries Android device for product model name and manufacturer."""
    adb = find_adb()
    if not adb or not serial:
        return "Android Device"
    try:
        res = subprocess.run([adb, "-s", serial, "shell", "getprop", "ro.product.model"],
                             capture_output=True, text=True, timeout=3)
        model = res.stdout.strip()
        brand_res = subprocess.run([adb, "-s", serial, "shell", "getprop", "ro.product.brand"],
                                   capture_output=True, text=True, timeout=3)
        brand = brand_res.stdout.strip().capitalize()
        if model:
            if brand and not model.lower().startswith(brand.lower()):
                return f"{brand} {model}"
            return model
    except Exception:
        pass
    return "Android Device"


def get_adb_devices() -> list[dict[str, Any]]:
    adb = find_adb()
    if not adb:
        return []
    try:
        res = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        devices = []
        saved_data = load_saved_phones().get("devices", {})
        for line in res.stdout.strip().splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                is_wifi = ":" in serial
                conn_type = "wifi" if is_wifi else "usb"
                saved_info = saved_data.get(serial, {})
                model = saved_info.get("model")
                if not model or model == "Android Device":
                    model = get_device_model(serial)
                pin = saved_info.get("pin", "")

                # Auto-record device in saved_phones.json
                save_phone_memory(serial=serial, pin=pin, model=model, connection_type=conn_type)

                devices.append({
                    "serial": serial,
                    "status": parts[1],
                    "model": model,
                    "connection_type": conn_type,
                    "has_saved_pin": bool(pin),
                    "masked_pin": "••••" if pin else "",
                    "auto_unlock": saved_info.get("auto_unlock", True),
                })
        return devices
    except Exception:
        return []


def switch_to_wireless_adb(device_serial: str = "") -> dict[str, Any]:
    """Switches a connected USB phone to Wireless ADB (port 5555) over Wi-Fi.
    Allows user to unplug the USB cable and continue full control wirelessly!"""
    adb = find_adb()
    if not adb:
        return {"ok": False, "error": "ADB is not installed. Download ADB via Phone Control Hub first."}

    adb_devs = get_adb_devices()
    if not adb_devs:
        return {"ok": False, "error": "No phone is currently connected. Plug in via USB cable first to enable wireless mode."}

    # Find target USB device
    target = ""
    target_model = "Android Device"
    if device_serial:
        for d in adb_devs:
            if d["serial"] == device_serial:
                target = d["serial"]
                target_model = d.get("model", "Android Device")
                break
    if not target:
        for d in adb_devs:
            if ":" not in d["serial"]:
                target = d["serial"]
                target_model = d.get("model", "Android Device")
                break
        if not target:
            target = adb_devs[0]["serial"]
            target_model = adb_devs[0].get("model", "Android Device")

    if ":" in target:
        return {"ok": True, "serial": target, "message": f"Device {target_model} ({target}) is already connected wirelessly via Wi-Fi!"}

    # 1. Enable TCP mode on port 5555
    res = subprocess.run([adb, "-s", target, "tcpip", "5555"], capture_output=True, text=True, timeout=8)
    if res.returncode != 0:
        return {"ok": False, "error": f"Failed to enable TCP mode: {res.stderr or res.stdout}"}

    time.sleep(1.0)

    # 2. Find phone's Wi-Fi IP address
    phone_ip = ""
    # Method A: ip route
    try:
        route_res = subprocess.run([adb, "-s", target, "shell", "ip", "route"], capture_output=True, text=True, timeout=5)
        m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)\b", route_res.stdout)
        if m:
            phone_ip = m.group(1)
    except Exception:
        pass

    # Method B: ip -f inet addr show wlan0
    if not phone_ip:
        try:
            addr_res = subprocess.run([adb, "-s", target, "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
                                      capture_output=True, text=True, timeout=5)
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", addr_res.stdout)
            if m:
                phone_ip = m.group(1)
        except Exception:
            pass

    # Method C: getprop dhcp.wlan0.ipaddress
    if not phone_ip:
        try:
            prop_res = subprocess.run([adb, "-s", target, "shell", "getprop", "dhcp.wlan0.ipaddress"],
                                      capture_output=True, text=True, timeout=4)
            candidate = prop_res.stdout.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", candidate):
                phone_ip = candidate
        except Exception:
            pass

    if not phone_ip:
        return {
            "ok": False,
            "error": "ADB TCP mode enabled on port 5555, but could not automatically detect phone's Wi-Fi IP. Please check phone Settings -> About -> IP address and connect manually."
        }

    # 3. Connect via ADB over Wi-Fi
    wifi_target = f"{phone_ip}:5555"
    conn_res = subprocess.run([adb, "connect", wifi_target], capture_output=True, text=True, timeout=10)
    out = (conn_res.stdout + conn_res.stderr).lower()

    if "connected" in out:
        saved_pin = get_saved_pin_for_device(target)
        save_phone_memory(serial=wifi_target, pin=saved_pin, model=target_model, connection_type="wifi")
        return {
            "ok": True,
            "serial": wifi_target,
            "ip": phone_ip,
            "model": target_model,
            "message": f"🎉 Switched to Wireless Wi-Fi ADB! Connected to {target_model} at {wifi_target}. You can now safely UNPLUG the USB cable and continue full control over Wi-Fi!",
        }
    else:
        return {
            "ok": False,
            "ip": phone_ip,
            "error": f"Enabled wireless mode, but 'adb connect {wifi_target}' returned: {conn_res.stdout or conn_res.stderr}. Ensure phone is on the same Wi-Fi network as this PC."
        }


def unlock_phone(pin: str = "", device_serial: str = "") -> dict[str, Any]:
    """Unlocks Android phone via ADB (remembering PIN across sessions) or triggers companion web unlock."""
    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []

    if adb and adb_devs:
        target = device_serial or adb_devs[0]["serial"]
        cmd_prefix = [adb, "-s", target] if target else [adb]
        target_model = adb_devs[0].get("model", "Android Device")

        # 1. Resolve PIN (use provided PIN, or retrieve remembered PIN)
        used_remembered_pin = False
        resolved_pin = pin.strip() if pin else ""
        if not resolved_pin:
            saved_pin = get_saved_pin_for_device(target)
            if saved_pin:
                resolved_pin = saved_pin
                used_remembered_pin = True

        try:
            # 1. Wake screen (KEYCODE_POWER or KEYCODE_WAKEUP)
            subprocess.run([*cmd_prefix, "shell", "input", "keyevent", "224"], capture_output=True, timeout=5)
            time.sleep(0.4)

            # 2. Dismiss keyguard / swipe up
            subprocess.run([*cmd_prefix, "shell", "input", "swipe", "500", "1600", "500", "400", "300"],
                           capture_output=True, timeout=5)
            time.sleep(0.5)

            # 3. Enter PIN or password if available
            pin_entered = False
            if resolved_pin:
                clean_pin = re.sub(r"[^0-9a-zA-Z]", "", resolved_pin)
                subprocess.run([*cmd_prefix, "shell", "input", "text", clean_pin], capture_output=True, timeout=5)
                time.sleep(0.3)
                # Press ENTER (KEYCODE_ENTER)
                subprocess.run([*cmd_prefix, "shell", "input", "keyevent", "66"], capture_output=True, timeout=5)
                pin_entered = True

                # Persist PIN into saved memory for future sessions
                save_phone_memory(
                    serial=target,
                    pin=clean_pin,
                    model=target_model,
                    connection_type="wifi" if ":" in target else "usb",
                )

            msg = (
                f"Unlocked {target_model} ({target}) using remembered PIN."
                if used_remembered_pin
                else (
                    f"Unlocked {target_model} ({target}) with PIN & saved to memory."
                    if pin_entered
                    else f"Dispatched Wake & Swipe Up to {target_model} ({target}). (Tip: Enter your PIN once and JARVIS will remember it automatically!)"
                )
            )

            return {
                "ok": True,
                "method": "adb",
                "device": target,
                "model": target_model,
                "pin_used": bool(resolved_pin),
                "remembered": used_remembered_pin or bool(resolved_pin),
                "message": msg,
            }
        except Exception as exc:
            return {"ok": False, "method": "adb", "error": str(exc)}

    from . import devices
    connected_web = devices.get_connected_phones()
    if connected_web:
        devices.queue_phone_event("wake", {"timestamp": time.time(), "message": "Wake request from JARVIS."})
        return {
            "ok": True,
            "method": "companion_web",
            "message": "Sent wake signal to connected phone companion. (Tip: For full PIN unlock & lockscreen bypass, plug phone into laptop with USB cable or switch to Wireless Wi-Fi ADB)."
        }

    return {
        "ok": False,
        "method": "none",
        "error": "No phone connected. Connect your phone using a USB cable (with USB Debugging enabled) or open the Web Companion link on your phone."
    }


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

    from . import devices
    connected_web = devices.get_connected_phones()
    if connected_web:
        if key in ("wake", "power"):
            devices.queue_phone_event("wake", {"timestamp": time.time(), "message": "Wake request from JARVIS."})
            return {"ok": True, "method": "companion_web", "message": "Dispatched screen wake signal to phone companion."}
        return {
            "ok": False,
            "method": "companion_web",
            "error": f"Native button '{key}' requires USB cable connection. Plug phone into laptop with USB cable and enable USB Debugging."
        }

    return {
        "ok": False,
        "method": "none",
        "error": "No phone connected. Connect via USB cable (with USB debugging enabled) or open the Web Companion on your phone."
    }


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


def _prune_screen_captures(max_keep: int = 5) -> None:
    """Keeps only the most recent screen captures to save disk and memory space."""
    try:
        files = sorted(SCREEN_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[max_keep:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass


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
                _prune_screen_captures(max_keep=5)
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


def make_phone_call(phone_number: str, direct: bool = True, serial: str = "") -> dict[str, Any]:
    """Places a phone call to specified number using connected Android phone via ADB.

    If direct=True, executes android.intent.action.CALL to place call immediately.
    If direct=False, opens the phone dialer with number filled (android.intent.action.DIAL).
    """
    clean_num = "".join(c for c in phone_number if c.isdigit() or c in ("+", "*", "#"))
    if not clean_num:
        return {"ok": False, "error": "Invalid phone number provided."}

    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []

    if not adb or not adb_devs:
        return {
            "ok": False,
            "error": "No Android phone connected via ADB. Please connect your phone via USB or Wi-Fi to make phone calls."
        }

    target = serial.strip() or adb_devs[0]["serial"]

    # Wake screen first
    subprocess.run([adb, "-s", target, "shell", "input", "keyevent", "224"],
                   capture_output=True, timeout=5, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(0.3)

    action = "android.intent.action.CALL" if direct else "android.intent.action.DIAL"
    cmd = [adb, "-s", target, "shell", "am", "start", "-a", action, "-d", f"tel:{clean_num}"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (proc.stdout or proc.stderr or "").strip()

        # If CALL action was blocked by security permission, gracefully fall back to DIAL
        if "SecurityException" in out or "permission" in out.lower() or proc.returncode != 0:
            fallback_cmd = [adb, "-s", target, "shell", "am", "start", "-a", "android.intent.action.DIAL", "-d", f"tel:{clean_num}"]
            fallback_proc = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=10, check=False,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {
                "ok": fallback_proc.returncode == 0,
                "action": "dial",
                "phone_number": clean_num,
                "message": f"Opened phone dialer with number {clean_num}. Press call on your phone.",
            }

        return {
            "ok": True,
            "action": "call" if direct else "dial",
            "phone_number": clean_num,
            "message": f"Successfully initiated call to {clean_num} on your phone.",
        }
    except Exception as exc:
        return {"ok": False, "error": f"Failed to place call: {exc}"}


def end_phone_call(serial: str = "") -> dict[str, Any]:
    """Hangs up or terminates an active phone call on the connected Android phone."""
    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []
    if not adb or not adb_devs:
        return {"ok": False, "error": "No phone connected via ADB to end call."}

    target = serial.strip() or adb_devs[0]["serial"]
    try:
        # KEYCODE_ENDCALL is keyevent 6
        subprocess.run([adb, "-s", target, "shell", "input", "keyevent", "6"],
                       capture_output=True, timeout=5, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": True, "message": "Dispatched end call (hang up) signal to phone."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def answer_phone_call(serial: str = "") -> dict[str, Any]:
    """Answers an incoming phone call on the connected Android phone."""
    adb = find_adb()
    adb_devs = get_adb_devices() if adb else []
    if not adb or not adb_devs:
        return {"ok": False, "error": "No phone connected via ADB to answer call."}

    target = serial.strip() or adb_devs[0]["serial"]
    try:
        # KEYCODE_CALL is keyevent 5, or input keyevent KEYCODE_HEADSETHOOK (79)
        subprocess.run([adb, "-s", target, "shell", "input", "keyevent", "5"],
                       capture_output=True, timeout=5, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": True, "message": "Dispatched answer call signal to phone."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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


def is_pc_sleeping() -> bool:
    """Returns True if PC is currently in sleep / low-power standby mode."""
    return _PC_SLEEP_ACTIVE


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

        time.sleep(1.0)

        # 2. Modern Standby S0 / ACPI suspend via PowerShell .NET Application::SetSuspendState
        try:
            ps_cmd = 'Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $true, $false)'
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                           capture_output=True, timeout=8)
        except Exception:
            pass

        # 3. For systems supporting traditional ACPI S3 sleep via ctypes powrprof
        try:
            SetSuspendState = ctypes.windll.powrprof.SetSuspendState
            SetSuspendState.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
            SetSuspendState.restype = ctypes.c_bool
            SetSuspendState(False, True, False)
        except Exception:
            pass

        # 4. For Modern Standby (S0 Low Power Idle) laptops, turning off monitor puts Windows into S0 sleep
        try:
            # HWND_BROADCAST = 0xFFFF, WM_SYSCOMMAND = 0x0112, SC_MONITORPOWER = 0xF170, 2 = Monitor Off
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
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
