# JARVIS v4.5 Remote Access & GitHub Hosting Guide

This guide explains how to host your JARVIS repository on GitHub, control your PC from your phone when you are away from home (on cellular 4G/5G or different networks), and wake your PC from sleep mode.

---

## 1. Pushing JARVIS to your GitHub Repository

Your project is already set up with git and clean ignore rules (excluding `.env`, database, and secrets).

### Quick Push Steps:
1. Open [GitHub.com](https://github.com) and create a **New Repository** (e.g. named `JARVIS_Free_Agent_v4`). You can make it **Private** or Public.
2. In your terminal inside the JARVIS directory, run:
```bash
git remote add origin https://github.com/YOUR_USERNAME/JARVIS_Free_Agent_v4.git
git branch -M main
git push -u origin main
```
3. Or double click `deploy_github.bat` in the project root to push automatically!

---

## 2. Remote PC Control When Away from Home (4G / 5G / Different Wi-Fi)

When your phone and PC are on **different networks** (e.g. phone on 4G/5G or outside the house):

1. **Start Remote HTTPS Tunnel**:
   - In the JARVIS PC Dashboard, navigate to the **Nearby & Phone** tab.
   - Click **Start Remote Tunnel**.
   - JARVIS leverages Windows built-in OpenSSH to launch an encrypted reverse SSH tunnel (zero third-party installations needed).
   - A public HTTPS link is generated, for example: `https://xxxx.a.pinggy.link` or `https://xxxx.lhr.life`.
2. **Open on your Phone**:
   - Open that HTTPS URL on your phone's browser (`https://.../phone`).
   - Enter your secure passcode (default `1234` or whatever passcode you set in the dashboard).
3. **Full Remote Control**:
   - **Live PC Screen Stream**: View your PC desktop live and tap to click coordinates on PC.
   - **Virtual Touchpad & Mouse**: Touch surface with smooth drag, left click, right click, and double click.
   - **Virtual Keyboard**: Send keystrokes and shortcuts (Windows key, Alt+Tab, Ctrl+C, Ctrl+V, Desktop).
   - **Window Controls**: Minimize, Maximize full size, Restore, or Close windows.
   - **Phone Unlock**: Send PIN unlock sequences to connected phones.
   - **Wake-on-LAN**: Power on your sleeping PC with a single tap.

---

## 3. Wake-on-LAN (WoL): Turn on Sleeping PC from Phone

### How it works:
When your PC enters Sleep or Standby mode, the network adapter stays powered in low-power listening mode. Sending a Wake-on-LAN (WoL) magic packet to the PC's MAC address triggers the hardware to wake the PC up instantly.

### One-Time Windows Setup:
1. Press `Win + X` and open **Device Manager**.
2. Expand **Network Adapters**, right-click your active Ethernet or Wi-Fi adapter -> **Properties**.
3. Go to the **Power Management** tab:
   - Check *"Allow this device to wake the computer"*.
   - Check *"Only allow a magic packet to wake the computer"*.
4. Go to the **Advanced** tab:
   - Ensure *"Wake on Magic Packet"* is set to **Enabled**.
5. Your PC MAC address is automatically detected in JARVIS:
   - Displayed in the Phone companion under **Power & Windows** tab.
   - Displayed in the PC Dashboard under **Nearby & Phone** tab.

### Waking the PC:
- On your phone companion, tap **Wake PC (WoL)**. The packet is broadcasted across the local network and wakes the PC!

---

## 4. Persistent Chat History & Continuous Auto-Learning

- **Chat History**: Click the **Chat history** tab in the sidebar to browse all past conversations, switch between sessions, or search through previous interactions.
- **Continuous Auto-Learning**: Every conversation turn automatically extracts user habits, projects, preferences, and coding styles, and remembers them forever in SQLite.
- **Smart Recall**: Before answering, JARVIS searches stored knowledge and injects relevant context so he gets smarter and more aligned with you every time you speak with him.

---

## 5. Complete, Unabbreviated Coding Intelligence

- **No Truncation**: Model token budgets are dynamically elevated up to 4096+ tokens for programming and engineering tasks.
- **Strict Anti-Truncation Mandate**: System instructions enforce full, production-ready code with all imports and functions—strictly forbidding `# ... existing code ...` or `# TODO` placeholders.
- **Auto-Continuation**: If an LLM response gets cut off mid-code, JARVIS detects the unclosed code block and automatically prompts the model to finish the implementation seamlessly.
- **Execution & File Writing**: Use `execute_python` to test algorithms and `write_code_file` to save complete `.py` files directly to your computer.
