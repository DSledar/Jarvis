from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Load .env BEFORE importing agent.py (it reads a few settings at import time).
ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
load_dotenv(ROOT / ".env")

if __package__:
    from . import devices, memory, phone_control, remote, settings, tools
    from .agent import STATE, diagnose, stream_jarvis, test_brain, warmup
else:
    import devices  # type: ignore
    import memory  # type: ignore
    import phone_control  # type: ignore
    import remote  # type: ignore
    import settings  # type: ignore
    import tools  # type: ignore
    from agent import STATE, diagnose, stream_jarvis, test_brain, warmup  # type: ignore

app = FastAPI(title="JARVIS Free Agent", version="4.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _is_private_or_local(host: str, client_ip: str) -> bool:
    h = host.split(":")[0].strip().lower()
    c = client_ip.split(":")[0].strip().lower()
    for ip in (h, c):
        if ip in ("localhost", "127.0.0.1", "testserver", "::1", "0.0.0.0"):
            return True
        if ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
                          "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
                          "172.28.", "172.29.", "172.30.", "172.31.")):
            return True
    return False


@app.middleware("http")
async def local_network_guard(request: Request, call_next):
    """Protects PC while allowing phone companion & LAN connections over the local network or remote tunnel."""
    host = request.headers.get("host", "")
    client_ip = request.client.host if request.client else ""
    path = request.url.path

    # Always allow phone portal and static assets across local network or remote tunnel
    if path.startswith(("/phone", "/api/phone", "/favicon.ico", "/assets", "/styles.css", "/app.js", "/api/remote", "/api/history")):
        return await call_next(request)

    # Check for valid phone token in headers or query params
    token = request.headers.get("x-phone-token") or request.query_params.get("token")
    if token and devices.validate_token(token):
        return await call_next(request)

    # Allow private LAN and localhost
    if _is_private_or_local(host, client_ip):
        return await call_next(request)

    return JSONResponse({"detail": "Forbidden host. Passcode authentication required for remote access."}, status_code=403)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(default="default", min_length=1, max_length=120)
    image: str | None = None


class ApproveRequest(BaseModel):
    id: str
    approve: bool


class ToolRequest(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class SettingsRequest(BaseModel):
    brains: list[dict[str, Any]]
    voice_lang: str | None = None


# ----------------------------------------------------------------------------- static files
@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


@app.get("/app.js")
async def app_js() -> FileResponse:
    return FileResponse(FRONTEND / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/styles.css")
async def styles_css() -> FileResponse:
    return FileResponse(FRONTEND / "styles.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
async def favicon() -> JSONResponse:
    return JSONResponse({}, status_code=204)


@app.get("/assets/{filename}")
async def asset(filename: str) -> FileResponse:
    candidate = FRONTEND / "assets" / filename
    if not candidate.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(candidate)


# ----------------------------------------------------------------------------- status
@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(warmup())


@app.get("/api/status")
async def status() -> dict:
    import psutil

    pub = settings.public()
    first = pub["brains"][0] if pub["brains"] else {}
    return {
        "status": "online",
        "brain": STATE["active"] or first.get("label", "none"),
        "model": first.get("model", ""),
        "brains": len(pub["brains"]),
        "ai_ready": STATE["model_ready"],
        "fallbacks": STATE["fallbacks"],
        "last_error": STATE["last_error"],
        "os": platform.system(),
        "tools": len(tools.TOOLS),
        "memories": len(memory.list_facts()),
        "cpu": psutil.cpu_percent(interval=None),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage(Path.home().anchor or "/").percent,
        "voice_lang": pub["voice_lang"],
        "pending": tools.pending_list(),
    }


@app.get("/api/diagnose")
async def diagnose_endpoint() -> dict:
    issues = await diagnose()
    return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues}


# ----------------------------------------------------------------------------- chat
@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    """Newline-delimited JSON events: status / tool / tool_result / final / error."""
    async def gen():
        try:
            async for ev in stream_jarvis(payload.message, payload.session_id, image=payload.image):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001
            yield json.dumps({"type": "error", "text": str(exc)}, ensure_ascii=False) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/history/clear")
async def history_clear(payload: dict) -> dict:
    memory.clear_history(str(payload.get("session_id", "default")))
    return {"status": "cleared"}


@app.get("/api/history/sessions")
async def history_sessions() -> dict:
    return {"sessions": memory.get_sessions()}


@app.get("/api/history/session/{session_id}")
async def history_session_messages(session_id: str) -> dict:
    return {"session_id": session_id, "messages": memory.get_session_messages(session_id)}


@app.post("/api/history/session/new")
async def history_session_new(payload: dict) -> dict:
    title = str(payload.get("title", ""))
    return memory.create_session(title)


@app.delete("/api/history/session/{session_id}")
async def history_session_delete(session_id: str) -> dict:
    return {"ok": memory.delete_session(session_id)}


@app.get("/api/history/search")
async def history_search(q: str = "") -> dict:
    return memory.search_history(q)


# ----------------------------------------------------------------------------- tools (used by every dashboard button)
@app.get("/api/tools")
async def list_tools() -> dict:
    return {"tools": tools.describe_tools()}


@app.post("/api/tool")
async def run_tool(payload: ToolRequest) -> dict:
    """Run one tool directly (no AI). Risky tools are queued for approval like always."""
    if payload.name not in tools.TOOLS:
        raise HTTPException(status_code=404, detail="Unknown tool")
    result = await asyncio.to_thread(tools.run_tool, payload.name, payload.args)
    return {"result": result, "ok": not tools.failed(result), "pending": tools.pending_list()}


@app.post("/api/approve")
async def approve(payload: ApproveRequest) -> dict:
    result = await asyncio.to_thread(tools.resolve_pending, payload.id, payload.approve)
    return {"result": result, "pending": tools.pending_list()}


@app.post("/api/pending/clear")
async def pending_clear() -> dict:
    return {"cleared": tools.clear_pending()}


# ----------------------------------------------------------------------------- memory
@app.get("/api/memory")
async def memory_list() -> dict:
    return {"facts": memory.list_facts()}


@app.post("/api/memory")
async def memory_add(payload: dict) -> dict:
    return {"result": memory.add_fact(str(payload.get("fact", ""))), "facts": memory.list_facts()}


@app.post("/api/memory/forget")
async def memory_forget(payload: dict) -> dict:
    return {"result": memory.remove_facts(str(payload.get("keyword", "")) or "\u0000"), "facts": memory.list_facts()}


# ----------------------------------------------------------------------------- settings (AI brains)
@app.get("/api/settings")
async def get_settings() -> dict:
    return {**settings.public(), "presets": settings.PRESETS}


@app.post("/api/settings")
async def save_settings(payload: SettingsRequest) -> dict:
    settings.update(payload.brains, payload.voice_lang)
    STATE.update(model_ready=False, last_error="", fallbacks=0)
    asyncio.create_task(warmup())
    return {"saved": True, **settings.public()}


@app.post("/api/brain/test")
async def brain_test(payload: dict) -> dict:
    """Test one brain exactly as typed in the form (a blank key falls back to the saved key)."""
    brain = dict(payload)
    saved = settings.get_brain(str(brain.get("id", ""))) or {}
    brain["api_key"] = str(brain.get("api_key") or "").strip() or saved.get("api_key", "")
    brain["kind"] = "ollama" if brain.get("kind") == "ollama" else "openai"
    brain["base_url"] = str(brain.get("base_url") or "").rstrip("/")
    if brain["kind"] == "ollama" and brain["base_url"].endswith("/v1"):
        brain["base_url"] = brain["base_url"][:-3]
    if not brain.get("model") or not brain["base_url"]:
        return {"ok": False, "text": "Fill in the address and the model name first."}
    return await test_brain(brain)


# ----------------------------------------------------------------------------- phone companion portal
@app.get("/phone", response_class=HTMLResponse)
async def phone_index() -> HTMLResponse:
    phone_html = FRONTEND / "phone.html"
    if not phone_html.is_file():
        raise HTTPException(status_code=404, detail="Phone companion page not found")
    return HTMLResponse(phone_html.read_text(encoding="utf-8"))


class PhoneAuthRequest(BaseModel):
    password: str
    device_name: str = ""
    battery: int | None = None


@app.post("/api/phone/auth")
async def phone_auth(payload: PhoneAuthRequest, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    session = devices.authenticate_phone(payload.device_name, client_ip, payload.password, ua)
    if not session:
        raise HTTPException(status_code=401, detail="Incorrect passcode. Check the passcode in JARVIS Nearby & Phone tab.")
    if payload.battery is not None:
        devices.update_phone_status(session["token"], battery=payload.battery, device_name=payload.device_name)
    return {
        "ok": True,
        "token": session["token"],
        "name": session["name"],
        "pc_name": socket.gethostname(),
        "pc_ip": devices.get_local_ip(),
    }


class PhonePingRequest(BaseModel):
    token: str
    battery: int | None = None
    device_name: str = ""


@app.post("/api/phone/ping")
async def phone_ping(payload: PhonePingRequest) -> dict:
    s = devices.validate_token(payload.token)
    if not s:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please re-enter passcode.")
    devices.update_phone_status(payload.token, battery=payload.battery, device_name=payload.device_name)
    events = devices.pop_phone_events(payload.token)
    return {"ok": True, "events": events, "server_time": time.time()}


class PhoneChatRequest(BaseModel):
    token: str
    message: str
    session_id: str = "phone"


@app.post("/api/phone/chat")
async def phone_chat(payload: PhoneChatRequest) -> dict:
    s = devices.validate_token(payload.token)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    from .agent import run_jarvis
    res = await run_jarvis(payload.message, payload.session_id)
    return {"text": res.get("text", "Done."), "error": bool(res.get("error"))}


class PhoneActionRequest(BaseModel):
    token: str
    action: str
    target: str = ""


@app.post("/api/phone/action")
async def phone_action(payload: PhoneActionRequest) -> dict:
    s = devices.validate_token(payload.token)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    act = payload.action.lower().strip()
    result = "Unknown action"
    if act == "minimize":
        result = tools.minimize_window(payload.target)
    elif act == "maximize":
        result = tools.maximize_window(payload.target)
    elif act == "restore":
        result = tools.restore_window(payload.target)
    elif act == "close":
        result = tools.close_window(payload.target)
    elif act == "desktop":
        result = tools.show_desktop()
    elif act == "lock":
        result = tools.lock_pc()
    elif act.startswith("volume_") or act in ("mute", "play_pause", "next", "previous"):
        result = tools.media_control(act)
    elif act == "screenshot":
        result = tools.take_screenshot()
    return {"ok": not tools.failed(result), "result": result}


class PhoneClipboardRequest(BaseModel):
    token: str
    text: str = ""
    direction: str = "to_pc"


@app.post("/api/phone/clipboard")
async def phone_clipboard(payload: PhoneClipboardRequest) -> dict:
    s = devices.validate_token(payload.token)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    import pyperclip
    if payload.direction == "to_pc":
        pyperclip.copy(payload.text)
        return {"ok": True, "message": "Text copied to PC clipboard."}
    else:
        txt = pyperclip.paste() or ""
        return {"ok": True, "text": txt}


@app.post("/api/phone/upload")
async def phone_upload(request: Request) -> dict:
    import base64
    content = b""
    filename = ""
    token = ""

    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        data = await request.json()
        token = str(data.get("token", ""))
        filename = str(data.get("filename", "")).strip() or f"phone_upload_{int(time.time())}.dat"
        raw_b64 = str(data.get("data", "")).strip()
        if raw_b64:
            content = base64.b64decode(raw_b64)
    else:
        try:
            form = await request.form()
            token = str(form.get("token", ""))
            upload_file = form.get("file")
            if upload_file:
                content = await upload_file.read()
                filename = getattr(upload_file, "filename", "") or f"phone_upload_{int(time.time())}.dat"
        except Exception:
            raise HTTPException(status_code=400, detail="Could not parse upload format")

    s = devices.validate_token(token)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not content:
        raise HTTPException(status_code=400, detail="No file content provided")

    is_img = any(filename.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"))
    dest_dir = Path.home() / ("Pictures" if is_img else "Downloads")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / filename
    target.write_bytes(content)
    return {"ok": True, "saved_path": str(target), "size": len(content)}


# ----------------------------------------------------------------------------- PC dashboard devices API
@app.get("/api/devices")
async def devices_get() -> dict:
    cfg = devices.get_phone_settings()
    local_ip = devices.get_local_ip()
    return {
        "local_ip": local_ip,
        "phone_url": f"http://{local_ip}:8000/phone",
        "password": cfg.get("password", "1234"),
        "connected_phones": devices.get_connected_phones(),
        "nearby_devices": devices.scan_nearby_devices(force=False),
    }


@app.post("/api/devices/scan")
async def devices_scan() -> dict:
    fresh = devices.scan_nearby_devices(force=True)
    return {"nearby_devices": fresh, "connected_phones": devices.get_connected_phones()}


@app.post("/api/devices/password")
async def devices_update_password(payload: dict) -> dict:
    pwd = str(payload.get("password", "")).strip()
    try:
        new_pwd = devices.set_phone_password(pwd)
        return {"ok": True, "password": new_pwd}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/devices/ring")
async def devices_ring() -> dict:
    n = devices.ring_all_phones()
    return {"ok": True, "alerted_phones": n}


@app.post("/api/devices/send")
async def devices_send(payload: dict) -> dict:
    title = str(payload.get("title", "JARVIS"))
    msg = str(payload.get("message", "")).strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    n = devices.push_notification_to_phones(title, msg)
    return {"ok": True, "sent_to": n}


# ----------------------------------------------------------------------------- Live Screen Streaming & Mobile Remote Control
@app.get("/api/phone/screen")
async def phone_screen_snapshot(token: str = "") -> Response:
    from fastapi.responses import Response
    if token and not devices.validate_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await asyncio.to_thread(phone_control.capture_pc_screen_bytes, 1280, 65)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/phone/screen/stream")
async def phone_screen_mjpeg_stream(token: str = "") -> StreamingResponse:
    """Continuous low-latency MJPEG stream (~16 FPS) for real-time live desktop viewing."""
    if token and not devices.validate_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def frame_generator():
        try:
            while True:
                frame = await asyncio.to_thread(phone_control.capture_pc_screen_bytes, 1024, 55)
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n" +
                    frame + b"\r\n"
                )
                await asyncio.sleep(0.06)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/phone/flashlight")
async def phone_flashlight_get() -> dict:
    return {"ok": True, "state": phone_control.get_phone_torch_state()}


@app.post("/api/phone/flashlight")
async def phone_flashlight_post(payload: dict) -> dict:
    tok = str(payload.get("token", ""))
    if tok and not devices.validate_token(tok):
        raise HTTPException(status_code=401, detail="Unauthorized")
    state = bool(payload.get("state", not phone_control.get_phone_torch_state()))
    return phone_control.set_phone_torch(state)


@app.post("/api/phone/vibrate")
async def phone_vibrate_post(payload: dict) -> dict:
    duration = int(payload.get("duration", 500))
    return phone_control.vibrate_phone(duration)


@app.post("/api/phone/speak")
async def phone_speak_post(payload: dict) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    return phone_control.speak_on_phone(text)


@app.post("/api/phone/photo_request")
async def phone_photo_request() -> dict:
    devices.queue_phone_event("snap_photo", {"message": "JARVIS requested a camera snapshot."})
    return {"ok": True, "message": "Photo snapshot requested from phone."}


class PhoneMouseRequest(BaseModel):
    token: str
    action: str  # move, click, right_click, double_click, scroll
    dx: float = 0
    dy: float = 0
    btn: str = "left"


@app.post("/api/phone/mouse")
async def phone_mouse(payload: PhoneMouseRequest) -> dict:
    s = devices.validate_token(payload.token)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    import pyautogui
    act = payload.action.lower()
    try:
        if act == "move":
            pyautogui.moveRel(int(payload.dx), int(payload.dy), _pause=False)
        elif act == "click":
            pyautogui.click(button=payload.btn)
        elif act == "right_click":
            pyautogui.rightClick()
        elif act == "double_click":
            pyautogui.doubleClick()
        elif act == "scroll":
            pyautogui.scroll(int(payload.dy))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class PhoneKeyboardRequest(BaseModel):
    token: str
    key: str = ""
    text: str = ""


@app.post("/api/phone/keyboard")
async def phone_keyboard(payload: PhoneKeyboardRequest) -> dict:
    s = devices.validate_token(payload.token)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    import pyautogui
    try:
        if payload.text:
            pyautogui.write(payload.text)
        elif payload.key:
            k = payload.key.lower()
            if "+" in k:
                pyautogui.hotkey(*[part.strip() for part in k.split("+")])
            else:
                pyautogui.press(k)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/phone/unlock")
async def phone_unlock_api(payload: dict) -> dict:
    tok = str(payload.get("token", ""))
    if tok and not devices.validate_token(tok):
        raise HTTPException(status_code=401, detail="Unauthorized")
    pin = str(payload.get("pin", ""))
    serial = str(payload.get("serial", ""))
    res = await asyncio.to_thread(phone_control.unlock_phone, pin, serial)
    return res


@app.get("/api/phone/wol_info")
async def phone_wol_info_api() -> dict:
    return phone_control.get_wol_info()


@app.post("/api/phone/wol_wake")
async def phone_wol_wake_api(payload: dict) -> dict:
    mac = str(payload.get("mac", "")).strip() or phone_control.get_mac_address()
    bcast = str(payload.get("broadcast", "255.255.255.255")).strip()
    try:
        phone_control.send_wake_on_lan(mac, bcast)
        return {"ok": True, "message": f"Wake packet sent to {mac}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/phone/sleep")
async def phone_sleep_api(payload: dict) -> dict:
    tok = str(payload.get("token", ""))
    if tok and not devices.validate_token(tok):
        raise HTTPException(status_code=401, detail="Unauthorized")
    msg = phone_control.sleep_pc()
    return {"ok": True, "message": msg}


@app.post("/api/phone/screen_upload")
async def phone_screen_upload_api(request: Request) -> dict:
    data = await request.json()
    token = str(data.get("token", ""))
    if not devices.validate_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    raw_img = str(data.get("image", ""))
    if "," in raw_img:
        raw_img = raw_img.split(",", 1)[1]
    if raw_img:
        import base64
        buf = base64.b64decode(raw_img)
        save_dest = phone_control.SCREEN_DIR / f"phone_upload_{int(time.time())}.png"
        save_dest.write_bytes(buf)
        return {"ok": True, "path": str(save_dest)}
    return {"ok": False, "error": "No image payload"}


@app.get("/api/phone/overview")
async def phone_overview_api() -> dict:
    adb_devs = phone_control.get_adb_devices()
    connected = devices.get_connected_phones()
    path = phone_control.capture_phone_screenshot()
    has_shot = bool(path and Path(path).is_file())
    local_ip = devices.get_local_ip()
    return {
        "ok": True,
        "connected_phones": connected,
        "adb_devices": adb_devs,
        "torch_state": phone_control.get_phone_torch_state(),
        "has_screenshot": has_shot,
        "screenshot_url": f"/api/phone/screenshot?t={int(time.time())}" if has_shot else "",
        "local_ip": local_ip,
        "portal_url": f"http://{local_ip}:8000/phone",
    }


@app.post("/api/phone/key")
async def phone_key_api(payload: dict) -> dict:
    key = str(payload.get("key", "")).strip().lower()
    serial = str(payload.get("serial", "")).strip()
    return await asyncio.to_thread(phone_control.send_phone_key, key, serial)


@app.get("/api/phone/screenshot")
async def phone_screenshot_view() -> Response:
    path = await asyncio.to_thread(phone_control.capture_phone_screenshot)
    if path and Path(path).is_file():
        return Response(content=Path(path).read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="No screenshot available")


@app.post("/api/phone/capture_request")
async def phone_capture_request_api() -> dict:
    return await asyncio.to_thread(phone_control.request_phone_screenshot)


# ----------------------------------------------------------------------------- remote tunnel API
@app.get("/api/remote/status")
async def remote_status_api() -> dict:
    return remote.get_tunnel_status()


@app.post("/api/remote/start")
async def remote_start_api(payload: dict) -> dict:
    prov = str(payload.get("provider", "pinggy"))
    return await asyncio.to_thread(remote.start_tunnel, prov)


@app.post("/api/remote/stop")
async def remote_stop_api() -> dict:
    return await asyncio.to_thread(remote.stop_tunnel)


@app.post("/api/remote/custom")
async def remote_custom_api(payload: dict) -> dict:
    url = str(payload.get("url", ""))
    return remote.set_custom_url(url)


