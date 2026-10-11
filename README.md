# JARVIS Free Agent v4

A Windows command center that controls your PC with free AI. No OpenAI key, no credits, no card.

## Start
1. Install Python 3.10+ (tick "Add to PATH").
2. Double-click `start_windows.bat`. JARVIS opens at http://127.0.0.1:8000.

JARVIS works right away for direct commands. Type or say any of these (English, Arabic or Kurdish):
- open Spotify, open Chrome, open WhatsApp, open VS Code, open any installed app
- play lofi on YouTube, search Spotify for The Weeknd, google flutter tutorials
- volume up, volume down, mute, pause, next song
- minimize Chrome, make it full size, restore window, close window
- ring my phone, find my phone, nearby devices
- run python print(123), calculate 50 * 4 + 10
- compound multi-tasks: "open Spotify and volume up", "minimize VS Code then open calculator"
- take a screenshot, show desktop, lock, new tab, what time is it
- remember that I study Flutter

These commands run instantly **without any AI model**, so they never hit a free-limit error.

## Access from your phone (Nearby phone companion)
1. Open the **Nearby & Phone** tab in the dashboard.
2. Note your PC's portal address (e.g. `http://192.168.1.15:8000/phone`) and passcode (default: `1234`).
3. Open the link in your phone's browser, enter the passcode, and you can:
   - Talk to JARVIS from anywhere on your Wi-Fi network.
   - Remotely minimise, maximise, or lock PC windows.
   - Send photos/files directly to your PC.
   - Ring your phone from your PC when misplaced!
   - Sync clipboards between PC and phone.

## Add a free AI brain (for chat, Python coding, and harder tasks)
Open the **AI core** tab.
- **Fastest to set up: Groq.** Create a free key at console.groq.com, pick "Groq" in the list, paste the key, press **Test this brain**, then **Save changes**. (Try `qwen-2.5-coder-32b` or `llama-3.3-70b-versatile`).
- **Gemini:** key from aistudio.google.com. Exceptional coding and multi-task reasoning (`gemini-2.5-flash`, `gemini-2.5-pro`).
- **Ollama (offline, private):** install from ollama.com, run `ollama pull qwen2.5-coder:7b` or `qwen2.5:7b`, pick "Ollama".

Brains are tried from top to bottom. Use the arrows to change the order. Free limits and model names change, so check the provider's site if a model stops working.

## The dashboard
- **Command center:** chat, quick actions, window minimize/maximize, live CPU/memory/disk, activity log.
- **AI core:** manage brains, coding models, and test them.
- **Agents and tools:** 48 tools including Python execution, code analysis, window controls, and phone tools.
- **Nearby & Phone:** phone companion link, passcode setup, connected devices, and phone ringing.
- **Memory:** things JARVIS remembers. Add, forget, clear chat history.
- **Media:** play/pause/next, volume, Spotify and YouTube search.
- **Security:** actions waiting for your approval, safety rules, full activity history.
- **Talk to JARVIS** starts a hands-free voice conversation. Use Chrome or Edge.

## Safety
- Only this PC can reach the server (127.0.0.1).
- Running commands, writing/deleting files and restart/shutdown always wait for your Approve.
- Move the mouse into any screen corner to stop mouse and keyboard automation.
- API keys are saved in `data/settings.json` on this PC and never shown in the browser.

## Honest limits
- "Play X on Spotify" opens the Spotify search; you press play. YouTube "play" opens the best match directly.
- Small local models are less reliable than cloud models for multi-step tasks.
- Kurdish (Sorani) voice may not exist in your browser. If JARVIS is silent in Kurdish, pick Arabic (Iraq) or English as the voice language.
- Built and tested on a Linux test machine with a simulated Windows app list. Please tell me if any Windows app does not open.

## Troubleshooting
- **Red or amber banner says AI chat is offline:** add a brain in AI core. Direct commands still work.
- **An app does not open:** open Agents and tools, press Try on `list_apps` to see what JARVIS can find, then try the exact name.
- **No voice:** press Test next to Voice replies. Check the browser tab is not muted.
