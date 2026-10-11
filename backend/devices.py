"""Nearby devices discovery and mobile companion gateway for JARVIS.

Allows nearby devices (such as Android or iPhone) to connect securely over the
local network using an access passcode/PIN. Supports two-way communication:
voice/text commands from phone, remote PC window/media control, file/photo transfers,
clipboard synchronization, battery monitoring, and phone ringing/finding.
"""
from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "data" / "settings.json"
SHARED_FILES = ROOT / "data" / "shared_phone_files"
SHARED_FILES.mkdir(parents=True, exist_ok=True)

# In-memory phone sessions: token -> {token, device_name, ip, battery, last_seen, created}
PHONE_SESSIONS: dict[str, dict[str, Any]] = {}
# Pending events for each connected phone: token -> list[dict]
PHONE_EVENTS: dict[str, list[dict[str, Any]]] = {}
# General broadcast events for all connected phones
BROADCAST_EVENTS: list[dict[str, Any]] = []

_SCAN_LOCK = threading.Lock()
_LAST_SCAN_TIME = 0.0
_CACHED_DEVICES: list[dict[str, Any]] = []


# --------------------------------------------------------------------------- settings & password
def get_phone_settings() -> dict[str, Any]:
    """Retrieve phone access configuration (passcode, enabled status)."""
    default = {"enabled": True, "password": "1234", "name": "JARVIS Link"}
    if SETTINGS_PATH.is_file():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            cfg = data.get("phone_access")
            if isinstance(cfg, dict):
                return {**default, **cfg}
        except Exception:
            pass
    return default


def set_phone_password(new_password: str) -> str:
    """Update the passcode required for phones to access JARVIS."""
    pwd = new_password.strip()
    if len(pwd) < 3:
        raise ValueError("Passcode must be at least 3 characters long.")
    data: dict[str, Any] = {}
    if SETTINGS_PATH.is_file():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cfg = data.get("phone_access", {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["password"] = pwd
    cfg["enabled"] = True
    data["phone_access"] = cfg
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return pwd


def verify_password(entered_pass: str) -> bool:
    """Verify entered password against stored passcode."""
    cfg = get_phone_settings()
    expected = str(cfg.get("password", "1234")).strip()
    return entered_pass.strip() == expected


# --------------------------------------------------------------------------- session management
def authenticate_phone(device_name: str, ip: str, password: str, user_agent: str = "") -> dict[str, Any] | None:
    """Verify password and create a phone session token."""
    if not verify_password(password):
        return None
    token = uuid.uuid4().hex
    device_type = _infer_device_type(device_name, user_agent)
    session = {
        "token": token,
        "name": device_name.strip() or device_type,
        "ip": ip,
        "type": device_type,
        "user_agent": user_agent,
        "battery": None,
        "last_seen": time.time(),
        "created": time.time(),
    }
    PHONE_SESSIONS[token] = session
    PHONE_EVENTS[token] = []
    return session


def validate_token(token: str) -> dict[str, Any] | None:
    """Check if token is valid and refresh its last seen timestamp."""
    s = PHONE_SESSIONS.get(token)
    if s:
        s["last_seen"] = time.time()
        return s
    return None


def update_phone_status(token: str, battery: int | None = None, device_name: str = "") -> None:
    s = PHONE_SESSIONS.get(token)
    if s:
        s["last_seen"] = time.time()
        if battery is not None:
            s["battery"] = battery
        if device_name:
            s["name"] = device_name


def get_connected_phones() -> list[dict[str, Any]]:
    """Return all currently active phone connections (active in the last 2 minutes)."""
    now = time.time()
    active = []
    for tok, s in list(PHONE_SESSIONS.items()):
        if now - s["last_seen"] < 120:  # active within 2 mins
            active.append({
                "token": tok,
                "name": s["name"],
                "ip": s["ip"],
                "type": s.get("type", "Phone"),
                "battery": s.get("battery"),
                "last_seen_sec": int(now - s["last_seen"]),
            })
    return active


# --------------------------------------------------------------------------- event queue for phones
def queue_phone_event(event_type: str, payload: dict[str, Any], target_token: str | None = None) -> None:
    """Push an event (notification, ring, clipboard, file) to phone(s)."""
    ev = {"id": uuid.uuid4().hex[:8], "type": event_type, "data": payload, "time": time.time()}
    if target_token and target_token in PHONE_EVENTS:
        PHONE_EVENTS[target_token].append(ev)
    else:
        # Broadcast to all connected phones
        for q in PHONE_EVENTS.values():
            q.append(ev)
        BROADCAST_EVENTS.append(ev)
        if len(BROADCAST_EVENTS) > 50:
            BROADCAST_EVENTS.pop(0)


def pop_phone_events(token: str) -> list[dict[str, Any]]:
    """Fetch and clear events queued for this specific phone."""
    q = PHONE_EVENTS.get(token, [])
    events = list(q)
    q.clear()
    return events


def ring_all_phones() -> int:
    """Send an immediate loud alarm/ring signal to all connected phones."""
    active = get_connected_phones()
    queue_phone_event("ring", {"message": "JARVIS is ringing your phone to locate it!", "duration": 15})
    return len(active)


def push_notification_to_phones(title: str, message: str) -> int:
    """Send a push notification to all connected phones."""
    active = get_connected_phones()
    queue_phone_event("notification", {"title": title, "message": message})
    return len(active)


# --------------------------------------------------------------------------- network & discovery
def get_local_ip() -> str:
    """Determine the PC's primary Wi-Fi / Local Area Network IPv4 address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _infer_device_type(name: str, ua: str) -> str:
    low = (name + " " + ua).lower()
    if "iphone" in low or "ios" in low:
        return "iPhone"
    if "ipad" in low:
        return "iPad"
    if "android" in low or "samsung" in low or "pixel" in low or "xiaomi" in low or "redmi" in low or "huawei" in low:
        return "Android Phone"
    if "tablet" in low:
        return "Tablet"
    if "windows" in low or "macintosh" in low or "linux" in low:
        return "PC / Laptop"
    return "Nearby Device"


def scan_nearby_devices(force: bool = False) -> list[dict[str, Any]]:
    """Scan local network (ARP table, ADB, and ping sweep) for nearby devices."""
    global _LAST_SCAN_TIME, _CACHED_DEVICES
    with _SCAN_LOCK:
        if not force and _CACHED_DEVICES and (time.time() - _LAST_SCAN_TIME < 40):
            return _CACHED_DEVICES

        devices: dict[str, dict[str, Any]] = {}
        my_ip = get_local_ip()

        # 1. Inspect ARP table
        try:
            out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([0-9a-fA-F-]{17})\s+(\w+)", line)
                if m:
                    ip, mac, kind = m.group(1), m.group(2).replace("-", ":").lower(), m.group(3)
                    if ip == my_ip or ip.endswith(".255") or ip.startswith("224.") or ip.startswith("239."):
                        continue
                    hostname = ""
                    try:
                        hostname = socket.gethostbyaddr(ip)[0]
                    except Exception:
                        pass
                    dev_type = "Nearby Device"
                    if ip.endswith(".1"):
                        dev_type = "Wi-Fi Router / Gateway"
                    elif hostname:
                        dev_type = _infer_device_type(hostname, "")

                    devices[ip] = {
                        "ip": ip,
                        "mac": mac,
                        "hostname": hostname or f"Device ({ip})",
                        "type": dev_type,
                        "status": "online",
                    }
        except Exception:
            pass

        # 2. Check ADB devices if adb is available
        try:
            adb_out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=3).stdout
            for line in adb_out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    model = "Android Phone"
                    for p in parts[2:]:
                        if p.startswith("model:"):
                            model = p.replace("model:", "")
                    devices[serial] = {
                        "ip": serial,
                        "mac": "ADB",
                        "hostname": model,
                        "type": "Android Phone (USB/ADB)",
                        "status": "connected",
                    }
        except Exception:
            pass

        # 3. Include already connected phone companion sessions
        for s in PHONE_SESSIONS.values():
            if time.time() - s["last_seen"] < 180:
                devices[s["ip"]] = {
                    "ip": s["ip"],
                    "mac": "Wi-Fi Companion",
                    "hostname": s["name"],
                    "type": s["type"],
                    "status": "paired & active",
                    "battery": s.get("battery"),
                }

        result = list(devices.values())
        _CACHED_DEVICES = result
        _LAST_SCAN_TIME = time.time()
        return result
