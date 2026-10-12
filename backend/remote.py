"""Remote Access & Tunnel Manager:
Enables secure remote access to JARVIS when away from home or on cellular (4G/5G).
Uses native Windows OpenSSH reverse tunneling (localhost.run / pinggy.io / serveo.net).
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = ROOT / "data" / "remote_settings.json"

STATE: dict[str, Any] = {
    "active": False,
    "url": "",
    "provider": "serveo",
    "pid": None,
    "started_at": 0,
    "last_error": "",
    "custom_url": "",
}

_PROCESS: subprocess.Popen | None = None
_LOCK = threading.RLock()

TUNNEL_PATTERNS = [
    re.compile(r"https://[a-zA-Z0-9_\-]+\.serveousercontent\.com"),
    re.compile(r"https://[a-zA-Z0-9_\-]+\.serveo\.net"),
    re.compile(r"https://[a-zA-Z0-9_\-]+\.lhr\.life"),
    re.compile(r"https://[a-zA-Z0-9_\-]+\.a\.pinggy\.link"),
    re.compile(r"https://[a-zA-Z0-9_\-]+\.pinggy\.link"),
]


def _load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"custom_url": "", "preferred_provider": "serveo"}


def _save_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_tunnel_status() -> dict[str, Any]:
    global _PROCESS
    with _LOCK:
        if _PROCESS is not None:
            poll = _PROCESS.poll()
            if poll is not None:
                STATE["active"] = False
                STATE["url"] = ""
                STATE["pid"] = None
                if not STATE["last_error"]:
                    STATE["last_error"] = f"Tunnel exited with code {poll}"

        return {
            "active": bool(STATE["active"] and STATE["url"]),
            "url": STATE["url"],
            "provider": STATE["provider"],
            "started_at": STATE["started_at"],
            "uptime_sec": int(time.time() - STATE["started_at"]) if STATE["active"] else 0,
            "last_error": STATE["last_error"],
            "custom_url": STATE["custom_url"],
        }


def _reader_thread(proc: subprocess.Popen, provider: str) -> None:
    try:
        for line in iter(proc.stdout.readline, ""):
            line_str = str(line).strip()
            if not line_str:
                continue

            # Ignore docs/admin banners
            if "admin.localhost.run" in line_str or "localhost.run/docs" in line_str:
                continue

            for pat in TUNNEL_PATTERNS:
                m = pat.search(line_str)
                if m:
                    found = m.group(0).rstrip(" \t\r\n.,;)")
                    with _LOCK:
                        if not STATE["url"]:
                            STATE["url"] = found
                            STATE["active"] = True
                            STATE["last_error"] = ""
                    break
    except Exception:
        pass

    with _LOCK:
        if _PROCESS == proc and proc.poll() is not None:
            STATE["active"] = False


def _kill_proc(proc: subprocess.Popen) -> None:
    try:
        if platform.system().lower() == "windows" and proc.pid:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=1)
    except Exception:
        pass



def start_tunnel(provider: str = "serveo", port: int = 8000) -> dict[str, Any]:
    """Starts a secure reverse SSH tunnel to expose port 8000 to the internet over HTTPS."""
    global _PROCESS

    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None and STATE["url"]:
            return get_tunnel_status()

        if _PROCESS is not None:
            _kill_proc(_PROCESS)
            _PROCESS = None

        ssh_bin = "C:\\Windows\\System32\\OpenSSH\\ssh.exe" if platform.system().lower() == "windows" else "ssh"
        if not Path(ssh_bin).is_file() and platform.system().lower() == "windows":
            ssh_bin = "ssh"

        null_file = "NUL" if platform.system().lower() == "windows" else "/dev/null"

        # Serveo is default and fastest; localhost.run is fallback
        if provider == "localhost_run":
            cmd = [
                ssh_bin,
                "-R", f"80:127.0.0.1:{port}",
                "nokey@localhost.run",
                "-o", "StrictHostKeyChecking=no",
                "-o", f"UserKnownHostsFile={null_file}",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
            ]
        elif provider == "pinggy":
            cmd = [
                ssh_bin,
                "-p", "443",
                "-R", f"0:127.0.0.1:{port}",
                "a.pinggy.io",
                "-o", "StrictHostKeyChecking=no",
                "-o", f"UserKnownHostsFile={null_file}",
                "-o", "ServerAliveInterval=15",
            ]
        else:
            provider = "serveo"
            cmd = [
                ssh_bin,
                "-R", f"80:127.0.0.1:{port}",
                "serveo.net",
                "-o", "StrictHostKeyChecking=no",
                "-o", f"UserKnownHostsFile={null_file}",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
            ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0,
            )
            _PROCESS = proc
            STATE.update({
                "active": False,
                "url": "",
                "provider": provider,
                "pid": proc.pid,
                "started_at": time.time(),
                "last_error": "",
            })

            t = threading.Thread(target=_reader_thread, args=(proc, provider), daemon=True)
            t.start()

        except Exception as exc:
            STATE["last_error"] = str(exc)
            return {"active": False, "error": str(exc)}

    # Wait up to 8 seconds for URL detection
    for _ in range(80):
        time.sleep(0.1)
        if STATE["url"]:
            break

    # If first provider failed to produce URL, auto-try secondary provider
    if not STATE["url"] and provider == "serveo":
        with _LOCK:
            if _PROCESS is not None:
                _kill_proc(_PROCESS)
                _PROCESS = None
        return start_tunnel(provider="localhost_run", port=port)

    return get_tunnel_status()


def stop_tunnel() -> dict[str, Any]:
    """Stops the active SSH tunnel and cleans up all processes."""
    global _PROCESS
    with _LOCK:
        if _PROCESS is not None:
            _kill_proc(_PROCESS)
            _PROCESS = None

        STATE["active"] = False
        STATE["url"] = ""
        STATE["pid"] = None
        STATE["last_error"] = ""
        return get_tunnel_status()


def set_custom_url(url: str) -> dict[str, Any]:
    clean_url = url.strip().rstrip("/")
    STATE["custom_url"] = clean_url
    cfg = _load_settings()
    cfg["custom_url"] = clean_url
    _save_settings(cfg)
    return get_tunnel_status()


_saved = _load_settings()
STATE["custom_url"] = _saved.get("custom_url", "")
