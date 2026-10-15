"""JARVIS agent v4.

1. Simple commands ("open Spotify", "volume up", "play X") run INSTANTLY through commands.py: no AI, no quota.
2. Everything else goes to an AI 'brain'. Several brains can be set up (Settings tab); if one fails
   (quota, offline, bad key) JARVIS automatically tries the next.
3. The user always gets a short answer, even when every brain is down.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import platform
import re
import time
from typing import Any, AsyncIterator

import httpx

if __package__:
    from . import commands, memory, settings, tools
else:
    import commands  # type: ignore
    import memory  # type: ignore
    import settings  # type: ignore
    import tools  # type: ignore

MAX_STEPS = int(os.getenv("JARVIS_MAX_STEPS", "8"))
NUM_CTX = int(os.getenv("JARVIS_NUM_CTX", "8192"))
TIMEOUT = float(os.getenv("JARVIS_TIMEOUT", "120"))
MAX_ANSWER_TOKENS = int(os.getenv("JARVIS_MAX_TOKENS", "2048"))
MAX_CODING_TOKENS = int(os.getenv("JARVIS_MAX_CODING_TOKENS", "4096"))

PERSONA = """You are JARVIS, an elite AI assistant, master Python software engineer, and Windows command center for {os}.
Friendly, confident, brilliant, and proactive. Answer in the SAME LANGUAGE the user wrote in (English, Kurdish, Arabic, etc.).

KURDISH LANGUAGE MASTERY (شارەزایی تەواو لە زمانی کوردی):
- When the user writes or speaks in Kurdish (کوردی), ALWAYS respond in authentic, fluent, grammatically flawless Kurdish (Sorani script).
- Use natural, idiomatic Central Kurdish (Sorani) phrasing such as: 'بەڵێ گەورەم', 'فەرموو گەورەم لە خزمەتدام', 'بەسەرچاو گەورەم', 'هەموو سیستەمەکان لە ئامادەباشیدان', 'فەرمانەکەت جێبەجێ کرا گەورەم'.
- Always use proper Kurdish letters (ئ، ە، و، ۆ، ێ، ڕ، ڵ، پ، چ، ژ، گ، ڤ) and natural Kurdish sentence structure.
- Maintain polite, respectful, and helpful Kurdish conversation, and never switch to English or Arabic unless requested.

CAPABILITIES & KNOWLEDGE:
- Master Python Programmer: You know all of Python 3, software engineering, algorithms, system scripts, debugging, automation, and libraries.
- Complete Production Code: NEVER truncate code, never use placeholders like '# TODO' or '# rest of code here'. Always write 100% complete, functional code from start to finish.
- Code Tools: Use `execute_python` to test and run Python code. Use `write_code_file` to save complete scripts directly to disk. Use `analyze_code` to inspect code and `python_doc` to lookup Python documentation.
- Window Management: Use `minimize_window` to minimize windows, `maximize_window` to make windows full-size, and `restore_window` or `close_window`.
- Phone Unlock & Full Remote Control: You can unlock the user's phone with `unlock_phone(pin=...)`, toggle phone flashlight with `toggle_phone_flashlight`, vibrate phone with `vibrate_phone`, speak on phone with `speak_on_phone`, ring phone with `ring_phone`, check battery and connection status with `get_phone_status`, capture phone screen with `see_phone_screen`, and execute ADB commands with `phone_adb`.
- Wake-on-LAN (WoL): You can wake a sleeping PC over the network with `wake_pc_lan`.
- Mobile & PC Screen Viewing: You can inspect the PC display screen with `see_pc_screen` and inspect the mobile screen with `see_phone_screen` or `take_screenshot`.
- Remote Access Outside Wi-Fi: Start secure remote HTTPS tunnel with `start_remote_tunnel` and inspect remote URL with `get_remote_url`.
- Multi-Task Handling: When the user gives you multiple tasks, execute each tool step sequentially and summarize the outcome.
- PC Control: `open_app`, `youtube_play`, `media_control`, `press_key`, `type_text`, `lock_pc`, `show_desktop`.
- Live Camera Vision: When the user asks about what is on camera or says 'Look at this' or 'What do you see?', analyze the camera visual frame with laser precision, noting objects, persons, colors, texts, and surroundings.
- Background Wake & Sleep Modes: When the user says 'Wake up JARVIS', greet them warmly with system readiness. When they say 'Goodbye JARVIS' or 'Go to sleep', confirm entering sleep mode politely.
- Memory & Continuous Learning: Remember lasting details with `remember`. You continuously recall past discussions and adapt to user habits.

COMMUNICATION STYLE:
- For simple actions or quick questions: keep your spoken answer concise, friendly, and natural.
- For coding and technical tasks: provide complete, unabbreviated, working code with clear explanations.
- NEVER claim an action was done unless the tool succeeded. If a tool requires approval (APPROVAL_REQUIRED), instruct the user to press Approve in the Security tab.
- Never reveal secret API keys.
- Today is {today}.
{facts}"""

def _is_coding_request(text: str) -> bool:
    low = text.lower()
    keywords = [
        "code", "python", "script", "function", "class", "program", "develop", "debug",
        "algorithm", "scraper", "automation", "gui", "backend", "frontend", "api", "html",
        "css", "javascript", "sql", "write code", "create a script", "write a python",
        "make a script", "solve", "hard work", "task", "coding", "syntax", "refactor",
        "tkinter", "fastapi", "flask", "django", "pandas", "numpy", "matplotlib", "bot"
    ]
    return any(k in low for k in keywords) or "def " in text or "import " in text or "class " in text

STATE: dict[str, Any] = {"model_ready": False, "last_error": "", "active": "", "fallbacks": 0}


# --------------------------------------------------------------------------- one call to one brain
def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def _inspect_image_quick(image_b64: str) -> str:
    """Inspects camera frame with OpenCV for real-time visual properties."""
    if not image_b64:
        return ""
    try:
        import base64
        import cv2
        import numpy as np
        raw = image_b64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        buf = base64.b64decode(raw)
        nparr = np.frombuffer(buf, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return ""
        h, w, _ = img.shape
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray))
        light_desc = "well lit" if brightness > 110 else "dimly lit" if brightness > 50 else "dark"
        faces_count = 0
        cascade_path = getattr(cv2, "data", None) and cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        if cascade_path and os.path.exists(cascade_path):
            cascade = cv2.CascadeClassifier(cascade_path)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
            faces_count = len(faces)
        details = [f"Webcam Frame: {w}x{h}", f"Lighting: {light_desc}"]
        if faces_count > 0:
            details.append(f"{faces_count} person/face(s) detected in front of camera")
        return f"[Live Camera Feed: {', '.join(details)}]"
    except Exception:
        return ""


def _to_ollama(msgs: list[dict]) -> list[dict]:
    out = []
    for m in msgs:
        content = m.get("content")
        images = []
        if isinstance(content, list):
            text_part = ""
            for item in content:
                if item.get("type") == "text":
                    text_part += item.get("text", "")
                elif item.get("type") == "image_url":
                    raw_url = item.get("image_url", {}).get("url", "")
                    if "," in raw_url:
                        raw_url = raw_url.split(",", 1)[1]
                    if raw_url:
                        images.append(raw_url)
            content = text_part

        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": content or "",
                        "tool_calls": [{"function": {"name": c["function"]["name"],
                                                     "arguments": _parse_args(c["function"].get("arguments"))}}
                                       for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": content, "tool_name": m.get("name", "")})
        else:
            entry = {"role": m["role"], "content": content}
            if images:
                entry["images"] = images
            out.append(entry)
    return out


def clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


class BrainError(RuntimeError):
    """A short, human-readable reason a brain could not answer."""


def _explain(brain: dict, status: int, text: str) -> BrainError:
    low = text.lower()
    name = brain.get("label") or brain["model"]
    if status == 429 or "quota" in low or "rate limit" in low or "resource_exhausted" in low:
        return BrainError(f"{name} ({brain['model']}) hit its free limit for now")
    if status in (401, 403) or "api key" in low and status >= 400:
        return BrainError(f"{name} rejected the API key")
    if status == 404 or "not found" in low:
        extra = f" Run: ollama pull {brain['model']}" if brain["kind"] == "ollama" else ""
        return BrainError(f"model '{brain['model']}' was not found.{extra}")
    return BrainError(f"{name} error {status}: {text[:160]}")


async def _call(brain: dict, messages: list[dict], use_tools: bool, max_tokens: int | None) -> dict:
    base = brain["base_url"].rstrip("/")
    ollama = brain["kind"] == "ollama"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if ollama:
                payload: dict[str, Any] = {"model": brain["model"], "messages": _to_ollama(messages), "stream": False,
                                           "keep_alive": "30m", "options": {"num_ctx": NUM_CTX, "temperature": 0.3}}
                if max_tokens:
                    payload["options"]["num_predict"] = max_tokens
                if use_tools:
                    payload["tools"] = tools.schemas()
                r = await client.post(f"{base}/api/chat", json=payload)
                if r.status_code == 400 and any(w in r.text.lower() for w in ("image", "vision", "multimodal", "media")):
                    # Strip raw images if Ollama model is text-only (OpenCV telemetry is still preserved in system prompt)
                    for m in payload["messages"]:
                        m.pop("images", None)
                    r = await client.post(f"{base}/api/chat", json=payload)
                if r.status_code == 400 and use_tools and "tool" in r.text.lower():
                    payload.pop("tools", None)
                    r = await client.post(f"{base}/api/chat", json=payload)
            else:
                payload = {"model": brain["model"], "messages": messages, "temperature": 0.3, "stream": False}
                if max_tokens:
                    payload["max_tokens"] = max_tokens
                if use_tools:
                    payload["tools"] = tools.schemas()
                headers = {"Authorization": f"Bearer {brain['api_key']}"} if brain.get("api_key") else {}
                r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
                if r.status_code == 400 and any(w in r.text.lower() for w in ("image", "vision", "multimodal", "media")):
                    # Strip images if target cloud model is text-only
                    clean_msgs = []
                    for m in messages:
                        c = m.get("content")
                        if isinstance(c, list):
                            txt = "".join(x.get("text", "") for x in c if x.get("type") == "text")
                            clean_msgs.append({**m, "content": txt})
                        else:
                            clean_msgs.append(m)
                    payload["messages"] = clean_msgs
                    r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
                if r.status_code == 400 and use_tools and "tool" in r.text.lower():  # model without tool support: chat only
                    payload.pop("tools", None)
                    r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        hint = "Ollama is not running or not installed (ollama.com)" if ollama else "no internet connection or wrong address"
        raise BrainError(f"cannot reach {brain.get('label') or brain['model']}: {hint}") from None
    except httpx.TimeoutException:
        raise BrainError(f"{brain.get('label') or brain['model']} took too long to answer") from None
    if r.status_code >= 400:
        raise _explain(brain, r.status_code, r.text)

    data = r.json()
    msg = data["message"] if ollama else data["choices"][0]["message"]
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c["function"]
        args = fn.get("arguments", {})
        calls.append({"id": c.get("id") or f"call{i}", "type": "function",
                      "function": {"name": fn["name"], "arguments": args if isinstance(args, str) else json.dumps(args)}})
    return {"content": clean(msg.get("content")), "tool_calls": calls}


async def _llm(messages: list[dict], use_tools: bool = True, max_tokens: int | None = None) -> dict:
    """Try each configured brain in order until one answers."""
    brains = settings.brains()
    if not brains:
        raise BrainError("no AI brain is set up. Open the AI Core tab and add one")
    errors: list[str] = []
    for i, brain in enumerate(brains):
        try:
            res = await _call(brain, messages, use_tools, max_tokens or MAX_ANSWER_TOKENS)
            STATE.update(active=brain.get("label") or brain["model"], model_ready=True, last_error="")
            if i:
                STATE["fallbacks"] += 1
            return res
        except BrainError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{brain.get('label') or brain['model']}: {exc}")
    STATE["last_error"] = "; ".join(errors)
    raise BrainError("; ".join(errors))


def _text_tool_calls(content: str) -> list[dict]:
    """Small models sometimes print the tool call as JSON text instead of a real tool call."""
    calls = []
    for m in re.finditer(r"\{.*\}", content or "", re.S):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        name = obj.get("name")
        args = obj.get("arguments", obj.get("parameters", {}))
        if name in tools.TOOLS:
            calls.append({"id": f"txt{len(calls)}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args) if isinstance(args, dict) else str(args)}})
    return calls


# --------------------------------------------------------------------------- main entry (streams progress events)
async def stream_jarvis(message: str, session_id: str, image: str | None = None) -> AsyncIterator[dict]:
    # 1) Instant direct commands (no AI needed if no image query)
    if not image:
        plan = commands.try_plan(message)
        if plan:
            results: list[str] = []
            used: list[str] = []
            for name, args in plan.steps:
                used.append(name)
                yield {"type": "tool", "name": name, "args": args}
                res = await asyncio.to_thread(tools.run_tool, name, args)
                results.append(res)
                yield {"type": "tool_result", "name": name, "result": res[:300], "ok": not tools.failed(res)}
            final = plan.say(results)
            memory.save_turn(session_id, message, final)
            yield {"type": "final", "text": final, "agent": "JARVIS", "tools_used": used, "pending": tools.pending_list(), "direct": True}
            return

    # 2) AI brain with tools
    is_coding = _is_coding_request(message)
    max_tokens_budget = MAX_CODING_TOKENS if is_coding else MAX_ANSWER_TOKENS

    facts = memory.list_facts()
    facts_txt = ("\nTHINGS YOU KNOW ABOUT THE USER:\n- " + "\n- ".join(facts)) if facts else ""
    system = PERSONA.format(os=platform.system(), today=datetime.datetime.now().strftime("%A %d %B %Y, %H:%M"), facts=facts_txt)

    if image:
        vis_feed = _inspect_image_quick(image)
        if vis_feed:
            system += f"\n\n{vis_feed}\nThe user is currently interacting with you with their Live Camera open."

    # Dynamic memory recall from past interactions and knowledge base
    recalled = memory.recall_relevant_context(message)
    if recalled:
        system += f"\n\n{recalled}"

    # Kurdish language detection and enforcement
    if commands._lang(message) in ("ku", "ckb"):
        system += (
            "\n\nKURDISH MANDATE (فەرمانی زمانی کوردی): "
            "بەکارهێنەر بە زمانی کوردی قسە دەکات. وەڵامەکەت دەبێت بە کوردیی سۆرانیی ڕەسەن و پاراو بێت. "
            "ڕەوان و ڕێزدار بە (نموونە: بەڵێ گەورەم، بە خۆشحاڵییەوە، فەرمانت جێبەجێ کرا)."
        )

    if is_coding:
        system += (
            "\n\nCRITICAL CODING MANDATE: The user needs a complete, working solution. "
            "NEVER summarize or omit code. Output 100% complete files with all functions, classes, imports, and edge cases. "
            "Do NOT output partial snippets with placeholders. If helpful, use write_code_file to save the script directly."
        )

    user_content: Any = [
        {"type": "text", "text": message},
        {"type": "image_url", "image_url": {"url": image}}
    ] if image else message

    msgs: list[dict] = [{"role": "system", "content": system}, *memory.load_history(session_id),
                        {"role": "user", "content": user_content}]
    used = []
    final = ""
    yield {"type": "status", "text": "Analyzing & Coding..." if is_coding else "Thinking..."}
    try:
        for _ in range(MAX_STEPS):
            reply = await _llm(msgs, max_tokens=max_tokens_budget)
            calls = reply["tool_calls"] or _text_tool_calls(reply["content"])
            if not calls:
                final = reply["content"]
                break
            msgs.append({"role": "assistant", "content": "" if not reply["tool_calls"] else reply["content"], "tool_calls": calls})
            for call in calls:
                fn = call["function"]
                name, args = fn["name"], _parse_args(fn.get("arguments"))
                used.append(name)
                yield {"type": "tool", "name": name, "args": args}
                result = await asyncio.to_thread(tools.run_tool, name, args)
                yield {"type": "tool_result", "name": name, "result": result[:300], "ok": not tools.failed(result)}
                msgs.append({"role": "tool", "tool_call_id": call.get("id", name), "name": name, "content": result})
        if not final:  # the model went quiet: ask once more, no tools
            msgs.append({"role": "user", "content": "Now answer me directly without tools."})
            final = (await _llm(msgs, use_tools=False, max_tokens=max_tokens_budget))["content"]
        if not final:
            last = next((m["content"] for m in reversed(msgs) if m["role"] == "tool"), "")
            final = last[:200] if last else "Sorry, I got no answer from the model. Please try again."

        # Anti-truncation auto-continuation: if code block is still open, continue writing
        if is_coding and final and final.count("```") % 2 == 1:
            yield {"type": "status", "text": "Continuing full code implementation..."}
            msgs.append({"role": "assistant", "content": final})
            msgs.append({"role": "user", "content": "Continue writing the code directly from where it stopped. Finish the entire program."})
            continuation = await _llm(msgs, use_tools=False, max_tokens=MAX_CODING_TOKENS)
            cont_txt = continuation.get("content", "").strip()
            if cont_txt:
                final += "\n" + cont_txt

    except BrainError as exc:
        # All brains failed. Say so plainly and remind the user what still works.
        yield {"type": "final", "text": f"My AI brain is unavailable: {exc}. Direct commands like 'open Spotify' or 'unlock phone' still work. Check the AI Core tab.",
               "agent": "JARVIS", "tools_used": used, "pending": tools.pending_list(), "error": True}
        return

    memory.save_turn(session_id, message, final)
    yield {"type": "final", "text": final, "agent": "JARVIS", "tools_used": used, "pending": tools.pending_list()}


async def run_jarvis(message: str, session_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    async for ev in stream_jarvis(message, session_id):
        if ev["type"] == "final":
            result = ev
    return result


# --------------------------------------------------------------------------- warm-up, diagnostics, brain test
async def warmup() -> None:
    """Load a local Ollama model into memory in the background (cloud brains need no warm-up)."""
    for brain in settings.brains():
        if brain["kind"] == "ollama":
            try:
                await _call(brain, [{"role": "user", "content": "Say ok."}], False, 4)
                STATE.update(model_ready=True, active=brain.get("label") or brain["model"])
            except Exception:  # noqa: BLE001
                pass
            return


async def test_brain(brain: dict) -> dict:
    t0 = time.time()
    try:
        res = await _call(brain, [{"role": "user", "content": "Reply with the single word: ready"}], False, 12)
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "text": res["content"][:80] or "(empty reply)"}
    except BrainError as exc:
        return {"ok": False, "text": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": str(exc)}


async def diagnose() -> list[dict]:
    """Plain-language problems for the dashboard banner. Only an error if NO brain can work."""
    brains = settings.brains()
    if not brains:
        return [{"level": "error", "text": "No AI brain is set up yet. Direct commands like 'open Spotify' work. For chat, add a free brain in the AI Core tab."}]
    reachable = False
    notes: list[str] = []
    for b in brains:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                if b["kind"] == "ollama":
                    r = await client.get(f"{b['base_url']}/api/tags")
                    names = [m["name"] for m in r.json().get("models", [])]
                    if any(x == b["model"] or x.split(":")[0] == b["model"].split(":")[0] and ":" not in b["model"] for x in names):
                        reachable = True
                    else:
                        notes.append(f"Ollama is running but '{b['model']}' is not downloaded (run: ollama pull {b['model']})")
                else:
                    headers = {"Authorization": f"Bearer {b['api_key']}"} if b.get("api_key") else {}
                    if not b.get("api_key"):
                        notes.append(f"{b.get('label')} has no API key")
                        continue
                    r = await client.get(f"{b['base_url'].rstrip('/')}/models", headers=headers)
                    if r.status_code in (401, 403):
                        notes.append(f"{b.get('label')} rejected the API key")
                    else:
                        reachable = True
        except Exception:  # noqa: BLE001
            notes.append("Ollama is not running or not installed (ollama.com)" if b["kind"] == "ollama"
                         else f"cannot reach {b.get('label')}")
    if reachable:
        return []
    return [{"level": "error", "text": "AI chat is offline: " + "; ".join(notes) + ". Direct commands still work."}]
