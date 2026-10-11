# Changelog

## v1.0.0 — foundation

- Initial JARVIS dashboard.
- OpenAI Agents SDK orchestrator + specialist agents.
- Web search tool.
- Local Windows allowlisted app tool.
- YouTube/Spotify search tools.
- SQLite session memory.
- Camera/location browser controls.
- Realtime WebRTC voice foundation.

## v1.0.1 — startup fix

- Fixed backend agent import paths so the documented Uvicorn command can find `backend/agent.py`.
- Load `.env` before importing the agent so model settings are applied.
- Updated the default text model to `gpt-6-luna`.
- Hardened the Windows start script and added troubleshooting instructions.

## v2.0.0 — free model + real PC control

- Removed OpenAI dependency (Agents SDK, hosted web search, Realtime voice). No API key or card needed.
- New agent loop for any OpenAI-compatible endpoint; default Ollama (`qwen2.5:7b`). Groq/OpenRouter/Gemini free tiers via `.env`.
- New `backend/tools.py`: 26 tools (apps, mouse, keyboard, windows, clipboard, media keys, screenshots, vision, files, shell, power).
- Approval cards in the dashboard for risky actions.
- Free voice via browser SpeechRecognition / speechSynthesis.
- Live CPU/RAM/disk gauges (were never filled before).
- Fixed Windows app paths (were double-escaped).
- `start_windows.bat` checks Ollama and downloads the model.

## v3.0.0 — it talks and always answers

- Ollama native API with 8192 context (v2's compatibility route truncated the prompt, causing empty replies); model keep-alive and background warm-up.
- Guaranteed final answer (re-ask without tools, then fall back to the tool result). Strips `<think>` blocks.
- Streaming progress events (`/api/chat/stream`) shown live in the chat.
- Voice replies on by default; hands-free voice conversation; language picker; sentence-by-sentence speech; test-voice button.
- `/api/diagnose` + dashboard banner with plain-language fixes.
- Long-term memory (`remember` / `forget`, SQLite facts injected into the prompt).
- Windows UI Automation tools `list_controls` / `click_control` (pywinauto).
- 30 tools total. Chat text enlarged.

## v4.0.0 — works without AI, never stuck on quota

- Direct command parser (`commands.py`): open/play/volume/search/screenshot/etc. run instantly with no AI. Understands typos and Arabic/Kurdish verbs.
- App launcher rewritten: finds every Start Menu app (including Store apps) via Get-StartApps and launches with shell:AppsFolder. Fixes apps outside PATH (Spotify) never opening. Falls back to URI scheme or website.
- New tools: youtube_play, list_apps, get_time, lock_pc, show_desktop. Media keys repeat for real volume changes. Failures now start with ERROR and are spoken honestly.
- Multiple AI brains with automatic fallback (Groq, Gemini, OpenRouter, Ollama, custom). Settings tab with Test button. Keys kept on this PC, never sent to the browser.
- Friendly errors instead of raw JSON (429 quota, bad key, offline). Answers capped short.
- Full redesign: bigger type, animated orb with listening/thinking/speaking states, six working tabs, real activity feed, approval cards, tool runner.
- Host-header check blocks DNS-rebinding. Start script no longer blocks when Ollama is missing.

## v4.5.0 — Supercharged Intelligence, Window Controls, Multi-Tasking & Phone Access

- **Python & Coding Master**: Built-in Python code execution sandbox (`execute_python`), code syntax & structure analyzer (`analyze_code`), and Python standard library inspector (`python_doc`). JARVIS can execute code to solve complex math, process data, test scripts, and figure out hard problems.
- **Window Management**: Control Windows applications directly with `minimize_window`, `maximize_window` (full size), `restore_window`, and `close_window`. Instant direct commands ("minimize Chrome", "maximize window", "make it full size", "صغر نافذة", "گەورەکردنەوە").
- **Multi-Task Understanding**: JARVIS can now parse and execute multiple compound tasks in a single sentence (e.g. "open Spotify and volume up", "minimize VS Code then open calculator and take a screenshot", Arabic/Kurdish compound commands).
- **Nearby Devices & Phone Access by Passcode**:
  - Secure Mobile Companion Web App accessible on any phone/tablet on Wi-Fi at `http://<PC_IP>:8000/phone`.
  - Protected by a user-configurable access passcode (default: `1234`).
  - Remote control PC from your phone (voice commands to JARVIS, window minimize/maximize, volume/media, lock PC, show desktop).
  - Two-way file & photo transfers (upload photos straight from phone to PC's Pictures folder).
  - Bidirectional clipboard synchronization.
  - "Ring Phone" feature: PC can trigger a loud alarm sound and vibration on connected phone to find it.
  - Live network device discovery scanning Wi-Fi and ARP table.
- **Expanded Brain Presets**: Added top coding brains including `qwen2.5-coder:7b`, `deepseek-r1:7b`, Groq `qwen-2.5-coder-32b`, Gemini 2.5 Pro, and OpenRouter coding models.
- **48 Total Tools**: New dedicated "Nearby & Phone" tab in the dashboard, quick action buttons on Command Center.
