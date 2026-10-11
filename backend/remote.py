"""Remote Access & Tunnel Manager:
Enables secure remote access to JARVIS when away from home or on cellular (4G/5G).
Uses native Windows OpenSSH reverse tunneling (pinggy.io / localhost.run) or custom tunnel URLs.
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
    "provider": "pinggy",
    "pid": None,
    "started_at": 0,
    "last_error": "",
    "custom_url": "",
}

_PROCESS: subprocess.Popen | None = None
_LOCK = threading.Lock()


def _load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"custom_url": "", "preferred_provider": "pinggy"}


def _save_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_tunnel_status() -> dict[str, Any]:
    global _PROCESS
    with _LOCK:
        # Check if process died
        if _PROCESS is not None:
            poll = _PROCESS.poll()
            if poll is not None:
                STATE["active"] = False
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
    url_pattern = re.compile(r"https://[a-zA-Z0-9_\-\.]+\.(?:a\.pinggy\.link|pinggy\.link|lhr\.life|serveo\.net)")
    general_url = re.compile(r"https://[a-zA-Z0-9_\-\.]+\.[a-zA-Z]{2,}(?::\d+)?(?:/[a-zA-Z0-9_\-\.]*)?")

    for line in iter(proc.stdout.readline, ""):
        line_str = str(line).strip()
        if not line_str:
            continue

        # Look for URL
        match = url_pattern.search(line_str) or general_url.search(line_str)
        if match and not STATE["url"]:
            candidate = match.group(0).rstrip(" \t\r\n.,;)")
            with _LOCK:
                STATE["url"] = candidate
                STATE["active"] = True
                STATE["last_error"] = ""

    # When stdout ends
    with _LOCK:
        if _PROCESS == proc:
            STATE["active"] = False


def start_tunnel(provider: str = "pinggy", port: int = 8000) -> dict[str, Any]:
    """Starts a secure reverse SSH tunnel to expose port 8000 to the internet over HTTPS."""
    global _PROCESS

    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None and STATE["url"]:
            return get_tunnel_status()

        # Clean old process
        if _PROCESS is not None:
            try:
                _PROCESS.terminate()
            except Exception:
                pass
            _PROCESS = None

        ssh_bin = "C:\\Windows\\System32\\OpenSSH\\ssh.exe" if platform.system().lower() == "windows" else "ssh"
        if not Path(ssh_bin).is_file() and platform.system().lower() == "windows":
            ssh_bin = "ssh"

        null_file = "NUL" if platform.system().lower() == "windows" else "/dev/null"

        if provider == "localhost_run":
            cmd = [
                ssh_bin,
                "-R", f"80:localhost:{port}",
                "nokey@localhost.run",
                "-o", "StrictHostKeyChecking=no",
                "-o", f"UserKnownHostsFile={null_file}",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
            ]
        elif provider == "serveo":
            cmd = [
                ssh_bin,
                "-R", f"80:localhost:{port}",
                "serveo.net",
                "-o", "StrictHostKeyChecking=no",
                "-o", f"UserKnownHostsFile={null_file}",
                "-o", "ServerAliveInterval=30",
            ]
        else:
            # Default pinggy
            provider = "pinggy"
            cmd = [
                ssh_bin,
                "-p", "443",
                "-R", f"0:localhost:{port}",
                "qr@a.pinggy.io",
                "-o", "StrictHostKeyChecking=no",
                "-o", f"UserKnownHostsFile={null_file}",
                "-o", "ServerAliveInterval=30",
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

            # Start thread to read URL from stdout
            t = threading.Thread(target=_reader_thread, args=(proc, provider), daemon=True)
            t.start()

        except Exception as exc:
            STATE["last_error"] = str(exc)
            return {"active": False, "error": str(exc)}

    # Wait up to 5 seconds for URL detection
    for _ in range(50):
        time.sleep(0.1)
        if STATE["url"]:
            break

    return get_tunnel_status()


def stop_tunnel() -> dict[str, Any]:
    """Stops the active SSH tunnel."""
    global _PROCESS
    with _LOCK:
        if _PROCESS is not None:
            try:
                _PROCESS.terminate()
                _PROCESS.wait(timeout=2)
            except Exception:
                try:
                    _PROCESS.kill()
                except Exception:
                    pass
            _PROCESS = None

        STATE["active"] = False
        STATE["url"] = ""
        STATE["pid"] = None
        return get_tunnel_status()


def set_custom_url(url: str) -> dict[str, Any]:
    """Sets a manual custom tunnel URL (e.g. from ngrok, Cloudflare, or dynamic DNS)."""
    clean_url = url.strip().rstrip("/")
    STATE["custom_url"] = clean_url
    cfg = _load_settings()
    cfg["custom_url"] = clean_url
    _save_settings(cfg)
    return get_tunnel_status()


# Load saved custom URL on import
_saved = _load_settings()
STATE["custom_url"] = _saved.get("custom_url", "")
