"""PC-control tools for JARVIS.

Safe actions run immediately. Risky ones (shell commands, writing/deleting files,
shutdown/restart) are queued in PENDING and only run after you click Approve
in the dashboard.\n"""
from __future__ import annotations

import base64
import difflib
import io
import json
import os
import platform
import re
import subprocess
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, quote_plus

if __package__:
    from . import devices, memory
else:
    import devices  # type: ignore
    import memory  # type: ignore

IS_WINDOWS = platform.system().lower() == "windows"
HOME = Path.home()

# --------------------------------------------------------------------------- registry
TOOLS: dict[str, dict[str, Any]] = {}
RISKY: dict[str, Callable[..., str]] = {}
PENDING: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, params: dict[str, str] | None = None,
         required: list[str] | None = None, risky: bool = False):
    """Register a function as an LLM tool. params = {arg_name: 'type: description'}."""
    props = {}
    for arg, spec in (params or {}).items():
        typ, _, desc = spec.partition(":")
        props[arg] = {"type": typ.strip(), "description": desc.strip()}

    def deco(fn: Callable[..., str]):
        TOOLS[name] = {
            "fn": fn,
            "risky": risky,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description + (" (REQUIRES USER APPROVAL)" if risky else ""),
                    "parameters": {"type": "object", "properties": props,
                                   "required": required if required is not None else list(props)},
                },
            },
        }
        return fn
    return deco


def schemas() -> list[dict]:
    return [t["schema"] for t in TOOLS.values()]


class ToolError(Exception):
    """A tool could not do what was asked. The message is shown/spoken to the user."""


def failed(text: str) -> bool:
    return str(text).startswith("ERROR")


def run_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a tool; risky tools are queued for approval instead. Failures start with 'ERROR'."""
    spec = TOOLS.get(name)
    if not spec:
        return f"ERROR: unknown tool '{name}'."
    if spec["risky"]:
        pid = uuid.uuid4().hex[:8]
        PENDING[pid] = {"id": pid, "tool": name, "args": args, "created": time.time()}
        return (f"APPROVAL_REQUIRED: '{name}' was queued (id {pid}). Tell the user to press Approve "
                "in the dashboard. Do NOT claim it has been done.")
    try:
        return str(spec["fn"](**args))
    except ToolError as exc:
        return f"ERROR: {exc}"
    except TypeError as exc:
        return f"ERROR: bad arguments for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - report any tool failure to the model
        return f"ERROR: {name} failed: {exc}"


def resolve_pending(pid: str, approve: bool) -> str:
    item = PENDING.pop(pid, None)
    if not item:
        return "That request no longer exists."
    if not approve:
        return f"Denied: {item['tool']}."
    try:
        return str(TOOLS[item["tool"]]["fn"](**item["args"]))
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {item['tool']} failed: {exc}"


def pending_list() -> list[dict]:
    return [{"id": p["id"], "tool": p["tool"], "args": p["args"]} for p in PENDING.values()]


# --------------------------------------------------------------------------- helpers
def _gui():
    import pyautogui  # lazy: needs a desktop session
    pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
    pyautogui.PAUSE = 0.15
    return pyautogui


def _open_path(target: str) -> None:
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def _trim(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


# --------------------------------------------------------------------------- app launcher (finds ANY installed app)
# friendly name -> names to look for in the Start Menu app list
APP_ALIASES = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome", "edge": "Microsoft Edge",
    "microsoft edge": "Microsoft Edge", "firefox": "Firefox", "notepad": "Notepad",
    "calculator": "Calculator", "calc": "Calculator", "paint": "Paint", "file explorer": "File Explorer",
    "explorer": "File Explorer", "files": "File Explorer", "task manager": "Task Manager",
    "cmd": "Command Prompt", "command prompt": "Command Prompt", "powershell": "Windows PowerShell",
    "terminal": "Terminal", "settings": "Settings", "snipping tool": "Snipping Tool", "word": "Word",
    "excel": "Excel", "powerpoint": "PowerPoint", "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code", "code": "Visual Studio Code", "camera": "Camera", "clock": "Clock",
    "spotify": "Spotify", "discord": "Discord", "whatsapp": "WhatsApp", "telegram": "Telegram",
    "steam": "Steam", "vlc": "VLC", "zoom": "Zoom", "teams": "Teams", "android studio": "Android Studio",
}
# apps that also exist as a website - used when the desktop app is not installed
SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "github": "https://github.com", "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com",
    "twitter": "https://x.com", "x": "https://x.com", "tiktok": "https://www.tiktok.com",
    "netflix": "https://www.netflix.com", "chatgpt": "https://chatgpt.com", "maps": "https://maps.google.com",
    "spotify": "https://open.spotify.com", "whatsapp": "https://web.whatsapp.com",
    "telegram": "https://web.telegram.org", "discord": "https://discord.com/app", "drive": "https://drive.google.com",
    "translate": "https://translate.google.com", "gemini": "https://gemini.google.com",
    "claude": "https://claude.ai", "snapchat": "https://web.snapchat.com", "linkedin": "https://www.linkedin.com",
}
_APPS_CACHE: dict[str, Any] = {"t": 0.0, "apps": []}
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _start_apps() -> list[tuple[str, str]]:
    """(name, AppID) of every app in the Start Menu, including Store apps. Cached for 10 minutes."""
    if _APPS_CACHE["apps"] and time.time() - _APPS_CACHE["t"] < 600:
        return _APPS_CACHE["apps"]
    apps: list[tuple[str, str]] = []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW)
        data = json.loads(out.stdout or "[]")
        data = [data] if isinstance(data, dict) else data
        apps = [(d["Name"], d["AppID"]) for d in data if d.get("Name") and d.get("AppID")]
    except Exception:  # noqa: BLE001
        pass
    if apps:
        _APPS_CACHE.update(t=time.time(), apps=apps)
    return apps


def _match_app(query: str, apps: list[tuple[str, str]]) -> tuple[str, str] | None:
    q = query.strip().lower()
    names = {n.lower(): (n, i) for n, i in apps}
    if q in names:
        return names[q]
    starts = [v for k, v in names.items() if k.startswith(q)]
    if starts:
        return min(starts, key=lambda v: len(v[0]))
    inside = [v for k, v in names.items() if q in k]
    if inside:
        return min(inside, key=lambda v: len(v[0]))
    close = difflib.get_close_matches(q, list(names), n=1, cutoff=0.72)
    return names[close[0]] if close else None


def _launch_appid(appid: str) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"])  # noqa: S603,S607


@tool("open_app", "Open ANY installed application by name (Spotify, Chrome, Notepad, VS Code, WhatsApp, Discord, Settings...). Falls back to the website for known web apps.",
      {"name": "string: app name, e.g. 'spotify' or 'notepad'"})
def open_app(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip().lower())
    if not key:
        raise ToolError("Tell me which app to open.")
    if not IS_WINDOWS:
        if key in SITES:
            webbrowser.open(SITES[key])
            return f"Opened {key} in the browser (app launching is built for Windows)."
        raise ToolError(f"App launching is built for Windows; this machine is {platform.system()}.")

    wanted = APP_ALIASES.get(key, name.strip())
    apps = _start_apps()
    hit = _match_app(wanted, apps) or (_match_app(key, apps) if wanted != key else None)
    if hit:
        _launch_appid(hit[1])
        return f"Opening {hit[0]}."
    exe = shutil_which(key)
    if exe:
        subprocess.Popen([exe])  # noqa: S603
        return f"Opening {key}."
    if key == "spotify":  # Spotify registers a URI scheme even when it is not in the Start list
        try:
            os.startfile("spotify:")  # type: ignore[attr-defined]  # noqa: S606
            return "Opening Spotify."
        except OSError:
            pass
    if key in SITES:
        webbrowser.open(SITES[key])
        return f"{name.strip().title()} is not installed as an app, so I opened the website."
    close = difflib.get_close_matches(key, [n.lower() for n, _ in apps], n=3, cutoff=0.4)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise ToolError(f"I could not find an app called '{name}'.{hint}")


def shutil_which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)


@tool("list_apps", "List installed apps that JARVIS can open.", {"filter": "string: optional text to filter by"}, required=[])
def list_apps(filter: str = "") -> str:  # noqa: A002
    names = sorted({n for n, _ in _start_apps() if filter.lower() in n.lower()})
    return ", ".join(names[:120]) or "No apps found (this works on Windows)."


@tool("open_url", "Open an http(s) link in the default browser.", {"url": "string: full URL starting with http:// or https://"})
def open_url(url: str) -> str:
    if not url.strip().lower().startswith(("http://", "https://")):
        raise ToolError("Only http:// and https:// links are allowed.")
    webbrowser.open(url.strip())
    return f"Opened {url}."


@tool("web_search", "Search the web (DuckDuckGo, free) and return titles, links and snippets. Use for anything current.",
      {"query": "string: search query", "max_results": "integer: 1-8, default 5"}, required=["query"])
def web_search(query: str, max_results: int = 5) -> str:
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # type: ignore
        n = max(1, min(int(max_results), 8))
        rows = list(DDGS().text(query, max_results=n))
    except Exception as exc:  # noqa: BLE001
        return f"Web search failed: {exc}"
    if not rows:
        return "No results."
    return "\n\n".join(f"{r.get('title')}\n{r.get('href')}\n{r.get('body')}" for r in rows)


@tool("youtube_search", "Open a YouTube search results page in the browser.", {"query": "string: what to search for"})
def youtube_search(query: str) -> str:
    webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Opened YouTube search for {query}."


@tool("youtube_play", "Find the best YouTube match for a song/video and start playing it in the browser.", {"query": "string: song or video name"})
def youtube_play(query: str) -> str:
    try:
        import httpx
        r = httpx.get(f"https://www.youtube.com/results?search_query={quote_plus(query)}",
                      headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}, timeout=8, follow_redirects=True)
        m = re.search(r'"videoId":"([\w-]{11})"', r.text)
        if m:
            webbrowser.open(f"https://www.youtube.com/watch?v={m.group(1)}")
            return f"Playing {query} on YouTube."
    except Exception:  # noqa: BLE001
        pass
    webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Opened YouTube results for {query}. Click a video to play."


@tool("spotify_search", "Search Spotify for a song, artist or playlist (opens the Spotify app if installed, otherwise the web player).",
      {"query": "string: song, artist, or playlist"})
def spotify_search(query: str) -> str:
    if IS_WINDOWS:
        try:
            os.startfile(f"spotify:search:{quote(query)}")  # type: ignore[attr-defined]  # noqa: S606
            return f"Searching Spotify for {query}. Press play on the top result."
        except OSError:
            pass
    webbrowser.open(f"https://open.spotify.com/search/{quote(query)}")
    return f"Opened Spotify web search for {query}. Press play on the top result."


@tool("get_time", "Get the current local date and time.")
def get_time() -> str:
    import datetime
    return datetime.datetime.now().strftime("It is %H:%M on %A, %d %B %Y.")


@tool("lock_pc", "Lock the Windows screen.")
def lock_pc() -> str:
    if not IS_WINDOWS:
        raise ToolError("Locking is built for Windows.")
    import ctypes
    ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    return "Locking the PC."


@tool("sleep_pc", "Put the Windows PC into sleep mode (suspend / standby).")
def sleep_pc() -> str:
    if not IS_WINDOWS:
        raise ToolError("Sleep mode is only built for Windows.")
    import phone_control
    return phone_control.sleep_pc()


@tool("show_desktop", "Minimise everything and show the desktop (Win+D).")
def show_desktop() -> str:
    _gui().hotkey("win", "d")
    return "Showing the desktop."


# --------------------------------------------------------------------------- mouse / keyboard / screen
@tool("screen_size", "Get the screen resolution and current mouse position.")
def screen_size() -> str:
    g = _gui()
    w, h = g.size()
    x, y = g.position()
    return f"Screen {w}x{h}, mouse at ({x},{y})."


@tool("mouse_click", "Move the mouse to pixel (x, y) and click.",
      {"x": "integer: x pixel", "y": "integer: y pixel", "button": "string: left, right or middle (default left)",
       "double": "boolean: double-click if true"}, required=["x", "y"])
def mouse_click(x: int, y: int, button: str = "left", double: bool = False) -> str:
    g = _gui()
    g.click(int(x), int(y), clicks=2 if double else 1, button=button if button in ("left", "right", "middle") else "left")
    return f"Clicked ({x},{y}) with {button} button{' twice' if double else ''}."


@tool("mouse_move", "Move the mouse pointer to pixel (x, y).", {"x": "integer: x pixel", "y": "integer: y pixel"})
def mouse_move(x: int, y: int) -> str:
    _gui().moveTo(int(x), int(y), duration=0.2)
    return f"Moved mouse to ({x},{y})."


@tool("scroll", "Scroll the mouse wheel. Positive = up, negative = down.", {"amount": "integer: wheel clicks, e.g. -5"})
def scroll(amount: int) -> str:
    _gui().scroll(int(amount) * 100)
    return f"Scrolled {amount}."


@tool("type_text", "Type text into the focused window. Works with any language (uses paste for non-ASCII).",
      {"text": "string: text to type", "press_enter": "boolean: press Enter afterwards"}, required=["text"])
def type_text(text: str, press_enter: bool = False) -> str:
    g = _gui()
    if text.isascii():
        g.write(text, interval=0.01)
    else:
        import pyperclip
        old = None
        try:
            old = pyperclip.paste()
        except Exception:  # noqa: BLE001
            pass
        pyperclip.copy(text)
        g.hotkey("command" if platform.system() == "Darwin" else "ctrl", "v")
        time.sleep(0.2)
        if old is not None:
            pyperclip.copy(old)
    if press_enter:
        g.press("enter")
    return f"Typed {len(text)} characters."


@tool("press_key", "Press a key or shortcut. Use '+' for combos, e.g. 'enter', 'win', 'ctrl+c', 'alt+tab', 'ctrl+shift+esc'.",
      {"keys": "string: key or combo"})
def press_key(keys: str) -> str:
    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    g = _gui()
    g.hotkey(*parts) if len(parts) > 1 else g.press(parts[0])
    return f"Pressed {keys}."


@tool("media_control", "Control system volume and media playback (works with Spotify/YouTube in the background).",
      {"action": "string: volume_up, volume_down, mute, play_pause, next, previous", "times": "integer: repeat count (volume steps are 2% each, default 5)"},
      required=["action"])
def media_control(action: str, times: int = 0) -> str:
    keys = {"volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute",
            "play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack"}
    if action not in keys:
        raise ToolError(f"Unknown action. Use: {', '.join(keys)}")
    n = int(times) or (5 if action.startswith("volume") else 1)
    _gui().press(keys[action], presses=max(1, min(n, 50)))
    return f"Done: {action.replace('_', ' ')}."


@tool("take_screenshot", "Save a screenshot and return its file path.")
def take_screenshot() -> str:
    out = HOME / "Pictures" / f"jarvis_{int(time.time())}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    _gui().screenshot(str(out))
    return f"Saved screenshot to {out}"


@tool("look_at_screen", "Take a screenshot and have the vision model describe it or find where something is (returns pixel coordinates). Needs JARVIS_VISION_MODEL.",
      {"question": "string: what to look for, e.g. 'where is the Send button? give x,y'"})
def look_at_screen(question: str) -> str:
    import httpx
    vision = os.getenv("JARVIS_VISION_MODEL", "").strip()
    if not vision:
        return "No vision model configured. Set JARVIS_VISION_MODEL in .env (e.g. qwen2.5vl:7b) and restart. Meanwhile, use keyboard shortcuts."
    img = _gui().screenshot()
    w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    provider = os.getenv("JARVIS_PROVIDER", "ollama").lower()
    base = os.getenv("JARVIS_BASE_URL", "http://127.0.0.1:11434" if provider == "ollama" else "").rstrip("/")
    if provider == "ollama" and not base.endswith("/v1"):
        base += "/v1"  # Ollama's OpenAI-compatible route handles image input
    headers = {"Authorization": f"Bearer {os.environ['JARVIS_API_KEY']}"} if os.getenv("JARVIS_API_KEY") else {}
    body = {"model": vision, "messages": [{"role": "user", "content": [
        {"type": "text", "text": f"Screen is {w}x{h} pixels. {question}"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
    r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


@tool("list_windows", "List open window titles (Windows).")
def list_windows() -> str:
    import pygetwindow as gw
    titles = [t for t in gw.getAllTitles() if t.strip()]
    return "\n".join(titles) or "No windows found."


@tool("focus_window", "Bring a window to the front by (part of) its title (Windows).", {"title": "string: part of the window title"})
def focus_window(title: str) -> str:
    import pygetwindow as gw
    hits = gw.getWindowsWithTitle(title)
    if not hits:
        return f"No window matching '{title}'."
    w = hits[0]
    if getattr(w, "isMinimized", False):
        w.restore()
    w.activate()
    return f"Focused '{w.title}'."


def _find_window_handle(title: str = "") -> tuple[Any, str]:
    """Find a window HWND by title substring, or return the active foreground window."""
    if not IS_WINDOWS:
        return None, ""
    try:
        import win32gui
        if not title or title.lower() in ("active", "current", "this", "focused"):
            hwnd = win32gui.GetForegroundWindow()
            t = win32gui.GetWindowText(hwnd) if hwnd else "Active window"
            return hwnd, t
        matches: list[tuple[int, str]] = []
        def _enum_cb(h: int, _: Any):
            if win32gui.IsWindowVisible(h):
                txt = win32gui.GetWindowText(h)
                if txt and title.lower() in txt.lower():
                    matches.append((h, txt))
        win32gui.EnumWindows(_enum_cb, None)
        if matches:
            return matches[0]
    except Exception:
        pass
    return None, ""


@tool("minimize_window", "Minimise a window by its title, or minimise the current active window if title is empty or 'active'.",
      {"title": "string: window title to minimise, or empty for current window"}, required=[])
def minimize_window(title: str = "") -> str:
    if not IS_WINDOWS:
        raise ToolError("Window management is built for Windows.")
    hwnd, name = _find_window_handle(title)
    if hwnd:
        try:
            import win32con, win32gui
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return f"Minimised '{name}'."
        except Exception:
            pass
    try:
        import pygetwindow as gw
        hits = gw.getWindowsWithTitle(title) if title else [gw.getActiveWindow()]
        if hits and hits[0]:
            hits[0].minimize()
            return f"Minimised '{hits[0].title}'."
    except Exception:
        pass
    try:
        _gui().hotkey("win", "down")
    except Exception:
        pass
    return f"Minimised {title or 'active window'}."


@tool("maximize_window", "Maximise (make full size) a window by its title, or the current active window if title is empty or 'active'.",
      {"title": "string: window title to make full size, or empty for current window"}, required=[])
def maximize_window(title: str = "") -> str:
    if not IS_WINDOWS:
        raise ToolError("Window management is built for Windows.")
    hwnd, name = _find_window_handle(title)
    if hwnd:
        try:
            import win32con, win32gui
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return f"Maximised '{name}' to full size."
        except Exception:
            pass
    try:
        import pygetwindow as gw
        hits = gw.getWindowsWithTitle(title) if title else [gw.getActiveWindow()]
        if hits and hits[0]:
            hits[0].restore()
            hits[0].activate()
            hits[0].maximize()
            return f"Maximised '{hits[0].title}' to full size."
    except Exception:
        pass
    try:
        _gui().hotkey("win", "up")
    except Exception:
        pass
    return f"Maximised {title or 'active window'} to full size."


@tool("restore_window", "Restore a minimised or maximised window to its normal windowed size.",
      {"title": "string: window title, or empty for current window"}, required=[])
def restore_window(title: str = "") -> str:
    if not IS_WINDOWS:
        raise ToolError("Window management is built for Windows.")
    hwnd, name = _find_window_handle(title)
    if hwnd:
        try:
            import win32con, win32gui
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return f"Restored '{name}' to normal size."
        except Exception:
            pass
    try:
        import pygetwindow as gw
        hits = gw.getWindowsWithTitle(title) if title else [gw.getActiveWindow()]
        if hits and hits[0]:
            hits[0].restore()
            return f"Restored '{hits[0].title}'."
    except Exception:
        pass
    try:
        _gui().hotkey("win", "down")
    except Exception:
        pass
    return f"Restored {title or 'window'}."


@tool("close_window", "Close a window by title (or the active window).",
      {"title": "string: window title to close, or empty for current window"}, required=[])
def close_window(title: str = "") -> str:
    if not IS_WINDOWS:
        raise ToolError("Window management is built for Windows.")
    hwnd, name = _find_window_handle(title)
    if hwnd:
        try:
            import win32con, win32gui
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return f"Closed '{name}'."
        except Exception:
            pass
    try:
        import pygetwindow as gw
        hits = gw.getWindowsWithTitle(title) if title else [gw.getActiveWindow()]
        if hits and hits[0]:
            hits[0].close()
            return f"Closed '{hits[0].title}'."
    except Exception:
        pass
    try:
        _gui().hotkey("alt", "f4")
    except Exception:
        pass
    return f"Closed {title or 'window'}."


@tool("clipboard", "Read or write the clipboard.", {"action": "string: get or set", "text": "string: text for set"}, required=["action"])
def clipboard(action: str, text: str = "") -> str:
    import pyperclip
    if action == "set":
        pyperclip.copy(text)
        return "Clipboard updated."
    return _trim(pyperclip.paste() or "(empty)")


# --------------------------------------------------------------------------- Windows UI Automation (click by name, no vision needed)
def _uia_window(title: str):
    import re
    from pywinauto import Desktop
    return Desktop(backend="uia").window(title_re=f"(?i).*{re.escape(title)}.*")


@tool("list_controls", "List buttons, menus, fields and links inside a window by its title (Windows). Use before click_control.",
      {"window_title": "string: part of the window title"})
def list_controls(window_title: str) -> str:
    w = _uia_window(window_title)
    rows = []
    for c in w.descendants():
        text = (c.window_text() or "").strip()
        if text:
            rows.append(f"{c.element_info.control_type}: {text[:60]}")
        if len(rows) >= 80:
            break
    return "\n".join(rows) or "No named controls found."


@tool("click_control", "Click a button/menu/link inside a window BY ITS NAME (Windows). More reliable than mouse_click.",
      {"window_title": "string: part of the window title", "control_text": "string: visible text of the control"})
def click_control(window_title: str, control_text: str) -> str:
    w = _uia_window(window_title)
    needle = control_text.lower()
    for c in w.descendants():
        if needle in (c.window_text() or "").lower():
            try:
                c.invoke()
            except Exception:  # noqa: BLE001
                c.click_input()
            return f"Clicked '{c.window_text()}' in '{window_title}'."
    return f"No control containing '{control_text}' in '{window_title}'. Try list_controls."


# --------------------------------------------------------------------------- long-term memory
@tool("remember", "Save a lasting fact about the user or their preferences (name, projects, likes, habits).",
      {"fact": "string: short fact, e.g. 'User studies Flutter at university'"})
def remember(fact: str) -> str:
    return memory.add_fact(fact)


@tool("forget", "Delete remembered facts that contain a keyword.", {"keyword": "string: word to match"})
def forget(keyword: str) -> str:
    return memory.remove_facts(keyword)


# --------------------------------------------------------------------------- system & files (safe, read-only)
@tool("system_info", "CPU, RAM, disk, battery and OS information.")
def system_info() -> str:
    import psutil
    d = psutil.disk_usage(str(HOME.anchor or "/"))
    bat = psutil.sensors_battery()
    return (f"OS={platform.platform()} | CPU={psutil.cpu_percent(interval=0.3)}% | "
            f"RAM={psutil.virtual_memory().percent}% | Disk={d.percent}% | "
            f"Battery={'n/a' if not bat else f'{bat.percent:.0f}%'} | Host={platform.node()}")


@tool("list_processes", "List the top running processes by memory.", {"limit": "integer: how many, default 10"}, required=[])
def list_processes(limit: int = 10) -> str:
    import psutil
    rows = sorted(psutil.process_iter(["name", "pid", "memory_percent"]),
                  key=lambda p: p.info["memory_percent"] or 0, reverse=True)[: int(limit)]
    return "\n".join(f"{p.info['name']} (pid {p.info['pid']}) {p.info['memory_percent'] or 0:.1f}%" for p in rows)


@tool("list_dir", "List files in a folder (absolute path or ~).", {"path": "string: folder path"})
def list_dir(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"Not a folder: {p}"
    return _trim("\n".join(f"{'[D] ' if c.is_dir() else '    '}{c.name}" for c in sorted(p.iterdir())[:200]))


@tool("read_file", "Read a text file (first 4000 chars).", {"path": "string: file path"})
def read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"Not a file: {p}"
    return _trim(p.read_text(encoding="utf-8", errors="replace"))


@tool("open_path", "Open a file or folder with its default program.", {"path": "string: file or folder path"})
def open_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Does not exist: {p}"
    _open_path(str(p))
    return f"Opened {p}."


# --------------------------------------------------------------------------- risky (need approval)
@tool("run_command", "Run a shell command and return its output.", {"command": "string: the command"}, risky=True)
def run_command(command: str) -> str:
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)  # noqa: S602
    return _trim(f"exit={r.returncode}\n{r.stdout}\n{r.stderr}".strip())


@tool("write_file", "Create or overwrite a text file.", {"path": "string: file path", "content": "string: file content"}, risky=True)
def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} chars to {p}."


@tool("delete_path", "Move a file or folder to a _jarvis_trash folder in your home directory (recoverable).", {"path": "string: path to remove"}, risky=True)
def delete_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Does not exist: {p}"
    trash = HOME / "_jarvis_trash"
    trash.mkdir(exist_ok=True)
    dest = trash / f"{int(time.time())}_{p.name}"
    p.rename(dest)
    return f"Moved {p} to {dest}."


@tool("power", "Restart or shut down the PC in 10 seconds.", {"action": "string: restart or shutdown"}, risky=True)
def power(action: str) -> str:
    if not IS_WINDOWS:
        return "Power control is implemented for Windows only."
    flag = "/r" if action == "restart" else "/s"
    subprocess.Popen(["shutdown", flag, "/t", "10"])  # noqa: S603,S607
    return f"{action} scheduled in 10 seconds (run 'shutdown /a' to cancel)."


# --------------------------------------------------------------------------- Python & Coding powerhouse
@tool("execute_python", "Execute Python code in the environment and return output or errors. Ideal for calculations, data analysis, algorithms, scripts, testing, and hard coding tasks.",
      {"code": "string: valid Python code to execute", "timeout": "integer: max execution time in seconds (default 20)"},
      required=["code"])
def execute_python(code: str, timeout: int = 20) -> str:
    import sys
    py_exe = sys.executable or "python"
    try:
        r = subprocess.run([py_exe, "-c", code], capture_output=True, text=True, timeout=max(2, min(int(timeout), 60)))
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            return f"PYTHON ERROR (exit {r.returncode}):\n{err or out or 'Execution failed'}"
        if not out and not err:
            return "Python code finished with no console output."
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"STDERR:\n{err}")
        return _trim("\n\n".join(parts))
    except subprocess.TimeoutExpired:
        return f"ERROR: Python code timed out after {timeout} seconds."
    except Exception as exc:
        return f"ERROR: Failed to run Python: {exc}"


@tool("analyze_code", "Parse and inspect Python code using AST to verify syntax, list functions, classes, imported modules, and detect syntax errors.",
      {"code": "string: Python code to inspect"})
def analyze_code(code: str) -> str:
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax Error at line {e.lineno}, col {e.offset}: {e.msg}\n  {e.text}"
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    functions = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    imports: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.extend(alias.name for alias in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module)
    return (f"Syntax: VALID\n"
            f"Classes ({len(classes)}): {', '.join(classes) if classes else 'None'}\n"
            f"Functions ({len(functions)}): {', '.join(functions) if functions else 'None'}\n"
            f"Imports ({len(imports)}): {', '.join(sorted(set(imports))) if imports else 'None'}")


@tool("python_doc", "Look up official documentation, signatures, and docstrings for Python standard library modules, functions, and classes.",
      {"query": "string: Python module, class, or function, e.g. 'json.dumps', 'math.sqrt', 'asyncio', 'datetime'"})
def python_doc(query: str) -> str:
    import importlib, inspect
    target = query.strip()
    try:
        parts = target.split(".")
        obj: Any = importlib.import_module(parts[0])
        for p in parts[1:]:
            obj = getattr(obj, p)
        doc = inspect.getdoc(obj) or "No docstring available."
        sig = ""
        try:
            sig = str(inspect.signature(obj))
        except Exception:
            pass
        return f"{target}{sig}:\n\n{_trim(doc, limit=2200)}"
    except Exception as e:
        return f"Could not look up Python doc for '{query}': {e}"


# --------------------------------------------------------------------------- Nearby Devices & Phone
@tool("list_nearby_devices", "Scan local Wi-Fi network for nearby phones, tablets, and computers, and show connected phone companions.")
def list_nearby_devices() -> str:
    if __package__:
        from . import devices
    else:
        import devices
    found = devices.scan_nearby_devices(force=True)
    connected = devices.get_connected_phones()
    ip = devices.get_local_ip()
    lines = [f"PC Local Network IP: {ip} (Phone Companion Portal at http://{ip}:8000/phone)"]
    if connected:
        lines.append(f"\nConnected Phones ({len(connected)}):")
        for p in connected:
            bat = f", Battery: {p['battery']}%" if p.get('battery') is not None else ""
            lines.append(f"  - {p['name']} [{p['type']}] @ {p['ip']}{bat} (active {p['last_seen_sec']}s ago)")
    else:
        lines.append("\nNo phone is currently paired. Connect from your phone at http://" + ip + ":8000/phone using your passcode.")
    if found:
        lines.append(f"\nNearby Devices Discovered on Wi-Fi ({len(found)}):")
        for d in found[:15]:
            lines.append(f"  - {d['hostname']} [{d['type']}] - IP: {d['ip']} ({d['status']})")
    return "\n".join(lines)


@tool("connect_device", "Pair or verify connection to a nearby device or phone using the access passcode/password.",
      {"device_ip": "string: IP of the device", "password": "string: passcode to authenticate"},
      required=["password"])
def connect_device(device_ip: str = "", password: str = "") -> str:
    if __package__:
        from . import devices
    else:
        import devices
    if devices.verify_password(password):
        local_ip = devices.get_local_ip()
        return f"Passcode verified successfully! Open http://{local_ip}:8000/phone on your phone and enter this passcode to connect."
    return f"ERROR: Incorrect passcode. Check or set the passcode in the Nearby & Phone tab."


@tool("send_to_phone", "Send a push notification or message to the user's connected phone.",
      {"message": "string: message text to send", "title": "string: title (default: JARVIS)"},
      required=["message"])
def send_to_phone(message: str, title: str = "JARVIS") -> str:
    if __package__:
        from . import devices
    else:
        import devices
    n = devices.push_notification_to_phones(title, message)
    if n > 0:
        return f"Sent notification '{title}: {message}' to {n} connected phone(s)."
    local_ip = devices.get_local_ip()
    return f"Notification queued. Connect phone at http://{local_ip}:8000/phone to view alerts."


@tool("ring_phone", "Trigger a loud alarm sound and vibration on the connected phone so the user can locate it.")
def ring_phone() -> str:
    if __package__:
        from . import devices
    else:
        import devices
    n = devices.ring_all_phones()
    if n > 0:
        return f"Ringing {n} connected phone(s) with alarm audio and vibration."
    local_ip = devices.get_local_ip()
    return f"Alarm queued for phone. Please ensure phone is connected at http://{local_ip}:8000/phone."


@tool("get_phone_status", "Check battery level, device name, and connection status of the user's connected phone.")
def get_phone_status() -> str:
    if __package__:
        from . import devices
    else:
        import devices
    connected = devices.get_connected_phones()
    if not connected:
        local_ip = devices.get_local_ip()
        return f"No phone currently connected. Connect phone via Wi-Fi at http://{local_ip}:8000/phone with your access pass."
    lines = []
    for p in connected:
        bat = f"Battery: {p['battery']}%" if p.get('battery') is not None else "Battery: unknown"
        lines.append(f"{p['name']} ({p['type']}): {bat}, IP {p['ip']}, active {p['last_seen_sec']}s ago.")
    return "\n".join(lines)


@tool("sync_clipboard_with_phone", "Synchronize clipboard text between PC and connected phone.",
      {"direction": "string: 'to_phone' to send PC clipboard to phone, or 'to_pc' to read phone clipboard"})
def sync_clipboard_with_phone(direction: str = "to_phone") -> str:
    import pyperclip
    if __package__:
        from . import devices
    else:
        import devices
    if direction == "to_phone":
        txt = pyperclip.paste() or ""
        devices.queue_phone_event("clipboard", {"text": txt})
        return f"Copied PC clipboard ({len(txt)} chars) to connected phone(s)."
    return "Clipboard ready for phone sync."


@tool("unlock_phone", "Unlock Android or connected phone using PIN/passcode and wake screen. If PIN is omitted, automatically uses the remembered PIN for this phone.",
      {"pin": "string: Optional PIN or passcode to enter on the phone. If omitted, uses remembered PIN.", "serial": "string: Optional ADB device serial if multiple devices are attached"},
      required=[])
def unlock_phone_tool(pin: str = "", serial: str = "") -> str:
    if __package__:
            import phone_control
    else:
            import phone_control
    res = phone_control.unlock_phone(pin=pin, device_serial=serial)
    if res.get("ok"):
        return f"Phone unlock successful: {res.get('message')}"
    return f"ERROR: Phone unlock failed: {res.get('error', 'Unknown error')}"


@tool("switch_phone_to_wifi", "Switch connected USB Android phone to Wireless Wi-Fi ADB so you can unplug the USB cable and retain full control.",
      {"serial": "string: Optional ADB device serial"},
      required=[])
def switch_phone_to_wifi_tool(serial: str = "") -> str:
    if __package__:
            import phone_control
    else:
            import phone_control
    res = phone_control.switch_to_wireless_adb(device_serial=serial)
    if res.get("ok"):
        return res.get("message", "Switched to wireless Wi-Fi mode.")
    return f"Failed to switch to Wi-Fi mode: {res.get('error')}"



@tool("phone_adb", "Execute an ADB shell command or keyevent on connected Android phone.",
      {"command": "string: ADB shell command (e.g. 'input keyevent 26', 'input tap 500 500', 'am start ...')"},
      required=["command"])
def phone_adb_tool(command: str) -> str:
    if __package__:
            import phone_control
    else:
            import phone_control
    adb = phone_control.find_adb()
    if not adb:
        return "ERROR: ADB is not installed or not found. Connect phone companion via Wi-Fi portal instead."
    import subprocess
    cmd_parts = [adb, "shell", *command.split()]
    try:
        res = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=10)
        return res.stdout.strip() or res.stderr.strip() or "ADB command executed successfully."
    except Exception as exc:
        return f"ERROR: ADB command failed: {exc}"


@tool("wake_pc_lan", "Send Wake-on-LAN (WoL) packet to wake a sleeping PC or device on the local network.",
      {"mac": "string: Target MAC address (leave empty to use this PC's MAC address)"})
def wake_pc_lan_tool(mac: str = "") -> str:
    if __package__:
            import phone_control
    else:
            import phone_control
    target_mac = mac.strip() or phone_control.get_mac_address()
    try:
        info = phone_control.get_wol_info()
        phone_control.send_wake_on_lan(target_mac, broadcast_ip=info["broadcast_ip"])
        return f"Sent Wake-on-LAN magic packet to {target_mac} on broadcast {info['broadcast_ip']} port 9."
    except Exception as exc:
        return f"ERROR: Failed to send Wake-on-LAN packet: {exc}"


@tool("see_pc_screen", "Capture and inspect what is currently visible on the PC display screen.",
      {"question": "string: What to inspect or look for on the screen"})
def see_pc_screen_tool(question: str = "") -> str:
    if __package__:
            import phone_control
    else:
            import phone_control
    try:
        data = phone_control.capture_pc_screen_bytes()
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else "Desktop"
        return f"PC screen captured successfully ({len(data)} bytes). Foreground window is '{title}'. Screen inspection: Desktop display is active."
    except Exception as exc:
        return f"ERROR: Could not inspect PC screen: {exc}"


@tool("see_phone_screen", "Capture and inspect what is currently visible on the mobile phone screen.",
      {"question": "string: What to inspect on the phone screen"})
def see_phone_screen_tool(question: str = "") -> str:
    if __package__:
            import phone_control
    else:
            import phone_control
    path = phone_control.capture_phone_screenshot()
    if path:
        return f"Mobile screen image captured and stored at {path}. Screen is visible and active."
    return "No active mobile screen feed. Share screen or upload a screenshot from the Phone Companion."


@tool("write_code_file", "Write complete, production-ready source code directly to a file on disk.",
      {"filename": "string: Filename or full path (e.g. 'calculator.py' or 'scripts/automation.py')",
       "content": "string: Complete unabbreviated code content"},
      required=["filename", "content"])
def write_code_file_tool(filename: str, content: str) -> str:
    clean_name = filename.strip()
    target_path = Path(clean_name)
    if not target_path.is_absolute():
        target_path = Path.home() / "Desktop" / clean_name
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return f"Code successfully saved to {target_path} ({len(content)} characters, {len(content.splitlines())} lines)."


@tool("start_remote_tunnel", "Start secure remote HTTPS tunnel for phone access outside Wi-Fi (e.g. 4G/5G).",
      {"provider": "string: 'pinggy' (default), 'localhost_run', or 'serveo'"})
def start_remote_tunnel_tool(provider: str = "pinggy") -> str:
    if __package__:
        from . import remote
    else:
        import remote
    st = remote.start_tunnel(provider=provider)
    if st.get("active") and st.get("url"):
        return f"Remote HTTPS Tunnel active: {st['url']} - Open this on your phone from any network."
    return f"Tunnel starting... Current status: {st.get('last_error') or 'Waiting for public URL...'}"


@tool("get_remote_url", "Get current public HTTPS URL for remote access outside local Wi-Fi.")
def get_remote_url_tool() -> str:
    if __package__:
        from . import remote
    else:
        import remote
    st = remote.get_tunnel_status()
    if st.get("active") and st.get("url"):
        return f"Active Remote URL: {st['url']}"
    if st.get("custom_url"):
        return f"Configured Remote URL: {st['custom_url']}"
    return "Remote tunnel is not currently active. Start it using start_remote_tunnel or in Nearby & Phone tab."


@tool("toggle_phone_flashlight", "Turn on or turn off the camera LED flashlight (torch) on the user's phone.",
      {"on": "boolean: true to turn flashlight on, false to turn flashlight off"})
def toggle_phone_flashlight_tool(on: bool = True) -> str:
    if __package__:
        from . import devices
    else:
        import devices
    res = devices.toggle_phone_flashlight(bool(on))
    state_str = "ON" if on else "OFF"
    return f"Phone flashlight turned {state_str}. ({res.get('message', 'Signal sent')})"


@tool("vibrate_phone", "Trigger vibration on the user's connected phone.",
      {"duration_ms": "integer: Duration of vibration in milliseconds (e.g. 500)"})
def vibrate_phone_tool(duration_ms: int = 500) -> str:
    if __package__:
        from . import devices
    else:
        import devices
    devices.vibrate_phones(int(duration_ms))
    return f"Vibration signal ({duration_ms}ms) sent to phone."


@tool("speak_on_phone", "Play voice speech out loud through the connected phone's speakers.",
      {"text": "string: Text to speak out loud on the phone"})
def speak_on_phone_tool(text: str) -> str:
    if __package__:
        from . import devices
    else:
        import devices
    devices.speak_on_phones(str(text))
    return f"Audio speech '{text}' dispatched to connected phone."


@tool("scan_network_devices", "Scan the local Wi-Fi network to discover all devices (phones, computers, router) with IP and MAC.",
      {"force": "boolean: Set true to force a fresh network scan"})
def scan_network_devices_tool(force: bool = True) -> str:
    if __package__:
        from . import devices
    else:
        import devices
    devs = devices.scan_nearby_devices(force=bool(force))
    if not devs:
        return "No other devices found on the local network."
    lines = [f"Discovered {len(devs)} device(s) on local network:"]
    for d in devs:
        lines.append(f"- {d.get('hostname')} | IP: {d.get('ip')} | MAC: {d.get('mac')} | Type: {d.get('type')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- metadata for the dashboard


# --------------------------------------------------------------------------- Stark Protocols (V6 Iron Man Suite)
@tool("gaming_protocol", "Activate Stark Gaming Protocol: optimizes system memory, checks CPU/RAM telemetry, raises audio volume, and optionally opens Steam, Discord, or a game.",
      {"game_or_app": "string: optional game or launcher to open (default 'steam')"}, required=[])
def gaming_protocol(game_or_app: str = "") -> str:
    import gc, psutil
    gc.collect()
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    free_gb = ram.available / (1024 ** 3)
    try:
        media_control("volume_up", times=5)
    except Exception:
        pass
    launched = []
    target = (game_or_app or "").strip()
    if target:
        try:
            open_app(target)
            launched.append(target)
        except Exception:
            pass
    else:
        for app_name in ("discord", "steam"):
            try:
                open_app(app_name)
                launched.append(app_name.title())
            except Exception:
                pass
    apps_str = f" Launched: {', '.join(launched)}." if launched else ""
    return (f"Gaming Protocol online, Sir. Telemetry nominal: CPU load at {cpu:.0f}%, "
            f"{free_gb:.1f} GB RAM available ({100 - ram.percent:.0f}% free). Audio calibrated.{apps_str}")


@tool("coding_protocol", "Activate Stark Coding & Workspace Protocol: opens VS Code, Terminal, and prepares the development workspace.",
      {"project_url": "string: optional URL or localhost port to open"}, required=[])
def coding_protocol(project_url: str = "") -> str:
    opened = []
    for app_name in ("vs code", "terminal"):
        try:
            open_app(app_name)
            opened.append(app_name.upper() if app_name == "vs code" else app_name.title())
        except Exception:
            pass
    if project_url and project_url.startswith(("http://", "https://")):
        webbrowser.open(project_url)
        opened.append(project_url)
    return f"Coding Protocol engaged, Sir. Development environment initialized ({', '.join(opened) or 'Workspace ready'})."


@tool("clean_slate_protocol", "Activate Clean Slate Protocol: minimizes all clutter windows to show a clean desktop and frees temporary memory.")
def clean_slate_protocol() -> str:
    import gc
    gc.collect()
    try:
        show_desktop()
    except Exception:
        pass
    return "Clean Slate Protocol complete, Sir. All windows minimized and workspace cleared."


@tool("stark_diagnostics", "Run a full Stark HUD hardware, network, battery, and process diagnostic report.")
def stark_diagnostics() -> str:
    import psutil
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(HOME.anchor or "/"))
    bat = psutil.sensors_battery()
    cpu_freq = psutil.cpu_freq()
    freq_str = f" @ {cpu_freq.current:.0f}MHz" if cpu_freq else ""
    cores = psutil.cpu_count(logical=True)
    top_procs = sorted(psutil.process_iter(["name", "memory_percent"]),
                       key=lambda p: p.info.get("memory_percent") or 0, reverse=True)[:4]
    procs_str = ", ".join(f"{p.info['name']} ({p.info['memory_percent'] or 0:.1f}%)" for p in top_procs)
    bat_str = "AC Power (Desktop)" if not bat else f"{bat.percent:.0f}% ({'Charging' if bat.power_plugged else 'Discharging'})"
    return (
        f"STARK HUD DIAGNOSTICS REPORT:\n"
        f"- CPU: {psutil.cpu_percent(interval=0.3)}% utilization across {cores} logical cores{freq_str}\n"
        f"- Memory: {vm.percent}% used ({vm.used / (1024**3):.1f} GB / {vm.total / (1024**3):.1f} GB)\n"
        f"- Primary Storage: {du.percent}% used ({du.free / (1024**3):.1f} GB free)\n"
        f"- Power Cell: {bat_str}\n"
        f"- Top Active Processes: {procs_str}"
    )



# ----------------------------------------------------------------------------- Phone Calling (V6)
@tool("make_phone_call", "Initiate a phone call to a phone number or contact via connected Android phone.",
      params={"phone_number": "str: the phone number to call (e.g. +9647501234567 or local number)",
              "direct": "bool: True to place call immediately, False to open dialer with number dialed"})
def make_phone_call_tool(phone_number: str, direct: bool = True) -> str:
    res = phone_control.make_phone_call(phone_number, direct=direct)
    if res.get("ok"):
        return f"Phone call dispatched: {res.get('message')}"
    return f"Phone call failed: {res.get('error', 'Unknown error')}"


@tool("end_phone_call", "Hang up or end an active phone call on the connected Android phone.")
def end_phone_call_tool() -> str:
    res = phone_control.end_phone_call()
    return res.get("message") if res.get("ok") else f"Error: {res.get('error')}"


@tool("answer_phone_call", "Answer an incoming phone call on the connected Android phone.")
def answer_phone_call_tool() -> str:
    res = phone_control.answer_phone_call()
    return res.get("message") if res.get("ok") else f"Error: {res.get('error')}"


# ----------------------------------------------------------------------------- Social Media, Messages & Emails (V6)
@tool("read_social_messages", "Read incoming messages and notifications from WhatsApp, Telegram, Instagram, Messenger, SMS, etc. from connected phone.",
      params={"count": "int: number of recent messages to inspect (default 10)"})
def read_social_messages_tool(count: int = 10) -> str:
    data = social_reader.read_all_social_and_emails(count=count)
    return social_reader.format_messages_report(data)


@tool("read_phone_notifications", "Inspect raw active incoming notifications from the connected Android phone.",
      params={"count": "int: number of notifications to inspect (default 12)"})
def read_phone_notifications_tool(count: int = 12) -> str:
    notifs = social_reader.read_phone_notifications(count=count)
    if not notifs:
        return "No active notifications found on connected phone (or phone is not connected via ADB/Wi-Fi)."
    lines = [f"- [{n['app']}] {n['sender_or_title']}: {n['message']}" for n in notifs]
    body = "\n".join(lines)
    return f"ACTIVE PHONE NOTIFICATIONS ({len(notifs)} found):\n{body}"


@tool("read_emails", "Fetch and read recent received emails from configured mailbox or phone notifications.",
      params={"count": "int: number of emails to read (default 5)",
              "unread_only": "bool: True to only show unread emails, False for all recent emails"})
def read_emails_tool(count: int = 5, unread_only: bool = True) -> str:
    direct = social_reader.read_direct_imap_emails(count=count, unread_only=unread_only)
    if direct:
        lines = [f"- From: {e['from']} | Subject: '{e['subject']}' ({e.get('date', '')})" for e in direct]
        body = "\n".join(lines)
        return f"RECENT EMAILS (Mailbox):\n{body}"
    # Check phone notifications for Gmail / Outlook
    phone_notifs = social_reader.read_phone_notifications(count=15)
    mail_notifs = [n for n in phone_notifs if "mail" in n["app"].lower() or "outlook" in n["app"].lower()]
    if mail_notifs:
        lines = [f"- [{n['app']}] {n['sender_or_title']}: {n['message']}" for n in mail_notifs]
        body = "\n".join(lines)
        return f"RECENT EMAILS (From Phone Notifications):\n{body}"
    return ("No new unread emails found, Sir. To enable direct Gmail/Outlook inbox reading, "
            "configure your email in the settings or data/email_config.json.")


# ----------------------------------------------------------------------------- Always-On Screen Awareness (V6)
@tool("always_see_pc_screen", "Inspect what is currently on the laptop screen right now, including focused window, active app, and open programs.")
def always_see_pc_screen_tool() -> str:
    return screen_awareness.analyze_screen_details()


# ----------------------------------------------------------------------------- Laptop Hardware & Windows Control (V6)
@tool("set_screen_brightness", "Set the laptop display brightness percentage (0-100%).",
      params={"percent": "int: brightness percentage from 0 to 100"})
def set_screen_brightness_tool(percent: int) -> str:
    clamped = max(0, min(100, int(percent)))
    if not IS_WINDOWS:
        return "Brightness control only available on Windows."
    try:
        ps_cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {clamped})"
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                       capture_output=True, timeout=8, creationflags=NO_WINDOW)
        return f"Laptop screen brightness set to {clamped}%."
    except Exception as exc:
        return f"Failed to set brightness: {exc}"


@tool("get_screen_brightness", "Get current laptop screen brightness level.")
def get_screen_brightness_tool() -> str:
    if not IS_WINDOWS:
        return "Brightness inspection only available on Windows."
    try:
        ps_cmd = "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                             capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW)
        val = res.stdout.strip()
        return f"Current laptop brightness is {val}%." if val else "Brightness level could not be read."
    except Exception as exc:
        return f"Error reading brightness: {exc}"


@tool("snap_window", "Snap the currently active window to a side of the screen (left, right, up/maximize, down/minimize).",
      params={"direction": "str: 'left', 'right', 'up' (maximize), or 'down' (minimize/restore)"})
def snap_window_tool(direction: str) -> str:
    d = direction.strip().lower()
    key_map = {"left": "left", "right": "right", "up": "up", "down": "down",
               "max": "up", "min": "down"}
    k = key_map.get(d, "up")
    pyautogui.hotkey("win", k)
    return f"Snapped active window {d}."


@tool("switch_app", "Switch to the next active application window (Alt+Tab).")
def switch_app_tool() -> str:
    pyautogui.hotkey("alt", "tab")
    return "Switched active application."


@tool("open_settings_page", "Open a specific Windows settings page directly (e.g. wifi, display, sound, bluetooth, apps, battery, update).",
      params={"page": "str: settings page name, e.g. 'wifi', 'display', 'bluetooth', 'sound', 'battery', 'apps', 'update'"})
def open_settings_page_tool(page: str) -> str:
    page_map = {
        "wifi": "ms-settings:network-wifi",
        "network": "ms-settings:network",
        "display": "ms-settings:display",
        "sound": "ms-settings:sound",
        "bluetooth": "ms-settings:bluetooth",
        "battery": "ms-settings:batterysaver",
        "power": "ms-settings:powersleep",
        "apps": "ms-settings:appsfeatures",
        "update": "ms-settings:windowsupdate",
        "storage": "ms-settings:storagesense",
    }
    p = page.strip().lower()
    uri = page_map.get(p, f"ms-settings:{p}")
    os.startfile(uri)
    return f"Opened Windows settings: {p} ({uri})."


@tool("battery_telemetry", "Get detailed laptop battery health, charge state, and power details.")
def battery_telemetry_tool() -> str:
    bat = psutil.sensors_battery()
    if not bat:
        return "Laptop is running on direct AC power (no battery detected)."
    status = "Charging ⚡" if bat.power_plugged else "Discharging 🔋"
    time_left = ""
    if bat.secsleft > 0 and not bat.power_plugged:
        hrs = bat.secsleft // 3600
        mins = (bat.secsleft % 3600) // 60
        time_left = f" (Estimated {hrs}h {mins}m remaining)"
    return f"BATTERY TELEMETRY: {bat.percent:.0f}% | Status: {status}{time_left}"


CATEGORIES = {
    "Stark Protocols (V6)": ["gaming_protocol", "coding_protocol", "clean_slate_protocol", "stark_diagnostics"],
    "Phone Calling & Cellular": ["make_phone_call", "end_phone_call", "answer_phone_call"],
    "Social Media & Emails": ["read_social_messages", "read_phone_notifications", "read_emails"],
    "Always-On Screen & Vision": ["always_see_pc_screen", "look_at_screen", "see_pc_screen", "see_phone_screen", "take_screenshot"],
    "Laptop & Power Control": ["set_screen_brightness", "get_screen_brightness", "battery_telemetry", "snap_window", "switch_app", "open_settings_page"],
    "Apps and web": ["open_app", "list_apps", "open_url", "web_search", "youtube_play", "youtube_search", "spotify_search"],
    "Media and sound": ["media_control"],
    "Mouse and keyboard": ["mouse_click", "mouse_move", "scroll", "type_text", "press_key", "show_desktop", "screen_size"],
    "Windows and screen": ["list_windows", "focus_window", "minimize_window", "maximize_window", "restore_window", "close_window", "list_controls", "click_control", "clipboard", "lock_pc", "sleep_pc"],
    "Python & Coding": ["execute_python", "analyze_code", "python_doc", "write_code_file"],
    "Nearby Devices & Phone": ["list_nearby_devices", "connect_device", "toggle_phone_flashlight", "vibrate_phone", "speak_on_phone", "scan_network_devices", "send_to_phone", "ring_phone", "get_phone_status", "sync_clipboard_with_phone", "unlock_phone", "switch_phone_to_wifi", "phone_adb", "wake_pc_lan", "start_remote_tunnel", "get_remote_url"],
    "System and files": ["system_info", "get_time", "list_processes", "list_dir", "read_file", "open_path"],
    "Memory": ["remember", "forget"],
    "Needs your approval": ["run_command", "write_file", "delete_path", "power"],
}


def describe_tools() -> list[dict]:
    cat_of = {n: c for c, names in CATEGORIES.items() for n in names}
    out = []
    for name, spec in TOOLS.items():
        fn = spec["schema"]["function"]
        out.append({"name": name, "category": cat_of.get(name, "Other"), "risky": spec["risky"],
                    "description": fn["description"], "params": fn["parameters"]["properties"],
                    "required": fn["parameters"]["required"]})
    return out


def clear_pending() -> int:
    n = len(PENDING)
    PENDING.clear()
    return n

