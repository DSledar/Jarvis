"""JARVIS Social Media & Email Reader Engine.

Reads incoming notifications and messages from connected Android phones
(WhatsApp, Telegram, Instagram, Messenger, SMS, Gmail) via ADB dumpsys,
and checks Windows notification center or configured IMAP mailboxes.
"""
from __future__ import annotations

import email
from email.header import decode_header
import imaplib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from . import phone_control

logger = logging.getLogger("jarvis.social_reader")

ROOT = Path(__file__).resolve().parents[1]
EMAIL_CONFIG_FILE = ROOT / "data" / "email_config.json"

APP_FRIENDLY_NAMES = {
    "com.whatsapp": "WhatsApp",
    "com.whatsapp.w4b": "WhatsApp Business",
    "org.telegram.messenger": "Telegram",
    "org.telegram.plus": "Telegram Plus",
    "com.instagram.android": "Instagram",
    "com.facebook.orca": "Facebook Messenger",
    "com.facebook.katana": "Facebook",
    "com.google.android.gm": "Gmail",
    "com.microsoft.office.outlook": "Outlook Mobile",
    "com.google.android.apps.messaging": "SMS / Messages",
    "com.android.mms": "SMS Messages",
    "com.viber.voip": "Viber",
    "com.snapchat.android": "Snapchat",
    "com.twitter.android": "X (Twitter)",
    "com.discord": "Discord",
    "com.zhiliaoapp.musically": "TikTok",
}


def load_email_config() -> dict[str, Any]:
    """Loads email settings from data/email_config.json."""
    if EMAIL_CONFIG_FILE.is_file():
        try:
            return json.loads(EMAIL_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "enabled": False,
        "imap_server": "imap.gmail.com",
        "port": 993,
        "email": "",
        "password": "",
        "use_ssl": True,
    }


def save_email_config(cfg: dict[str, Any]) -> bool:
    """Saves email credentials and configuration."""
    try:
        EMAIL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        EMAIL_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.error("Failed to save email config: %s", exc)
        return False


def _decode_mime_words(raw: str) -> str:
    """Safely decodes RFC 2047 MIME encoded words in email headers."""
    if not raw:
        return ""
    try:
        decoded_fragments = decode_header(raw)
        out = []
        for frag, encoding in decoded_fragments:
            if isinstance(frag, bytes):
                out.append(frag.decode(encoding or "utf-8", errors="replace"))
            else:
                out.append(str(frag))
        return "".join(out)
    except Exception:
        return raw


def read_direct_imap_emails(count: int = 5, unread_only: bool = True) -> list[dict[str, Any]]:
    """Connects to configured IMAP server and fetches recent emails."""
    cfg = load_email_config()
    if not cfg.get("enabled") or not cfg.get("email") or not cfg.get("password"):
        return []

    results: list[dict[str, Any]] = []
    mail = None
    try:
        server = cfg.get("imap_server", "imap.gmail.com")
        port = int(cfg.get("port", 993))
        mail = imaplib.IMAP4_SSL(server, port)
        mail.login(cfg["email"], cfg["password"])
        mail.select("INBOX", readonly=True)

        criterion = "UNSEEN" if unread_only else "ALL"
        status, messages = mail.search(None, criterion)
        if status != "OK" or not messages[0]:
            if unread_only:
                # Fallback to last N emails if no unread
                status, messages = mail.search(None, "ALL")

        if status == "OK" and messages[0]:
            id_list = messages[0].split()
            # Fetch latest
            latest_ids = id_list[-count:]
            latest_ids.reverse()

            for msg_id in latest_ids:
                res, data = mail.fetch(msg_id, "(RFC822.HEADER)")
                if res != "OK":
                    continue
                for response_part in data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject = _decode_mime_words(msg.get("Subject", "(No Subject)"))
                        from_hdr = _decode_mime_words(msg.get("From", "Unknown Sender"))
                        date_hdr = msg.get("Date", "")
                        results.append({
                            "type": "email",
                            "app": "Email Inbox",
                            "from": from_hdr,
                            "subject": subject,
                            "date": date_hdr,
                            "unread": unread_only,
                        })
    except Exception as exc:
        logger.warning("IMAP fetch failed: %s", exc)
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass

    return results


def read_phone_notifications(count: int = 12, serial: str = "") -> list[dict[str, Any]]:
    """Queries connected Android phone via ADB for active incoming notifications.

    Parses notifications from WhatsApp, Telegram, Instagram, Messenger, SMS, Gmail.
    """
    adb = phone_control.find_adb()
    if not adb:
        return []

    target_serial = serial.strip()
    if not target_serial:
        devs = phone_control.get_adb_devices()
        if devs:
            target_serial = devs[0].get("serial", "")

    cmd = [adb]
    if target_serial:
        cmd.extend(["-s", target_serial])
    cmd.extend(["shell", "dumpsys", "notification", "--noredact"])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout:
            # Fallback without --noredact
            cmd_fallback = [adb]
            if target_serial:
                cmd_fallback.extend(["-s", target_serial])
            cmd_fallback.extend(["shell", "dumpsys", "notification"])
            proc = subprocess.run(
                cmd_fallback,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        output = proc.stdout or ""
    except Exception as exc:
        logger.warning("Failed to run ADB dumpsys notification: %s", exc)
        return []

    return parse_notification_dump(output, max_count=count)


def parse_notification_dump(text: str, max_count: int = 15) -> list[dict[str, Any]]:
    """Extracts structured notification entries from Android dumpsys notification text."""
    notifications: list[dict[str, Any]] = []
    if not text:
        return notifications

    # Split into NotificationRecord chunks
    records = re.split(r"NotificationRecord\(", text)
    for rec in records[1:]:
        # Extract pkg
        pkg_match = re.search(r"pkg=([a-zA-Z0-9_\.]+)", rec)
        if not pkg_match:
            continue
        pkg = pkg_match.group(1).strip()

        # Ignore system background noise
        if pkg in ("android", "com.android.systemui", "com.android.providers.downloads", "com.google.android.gms"):
            continue

        app_name = APP_FRIENDLY_NAMES.get(pkg, pkg.split(".")[-1].capitalize())

        # Extract title and text
        title_match = re.search(r"android\.title=(?:String\s*\((.*?)\)|([^\n,]+))", rec)
        text_match = re.search(r"android\.text=(?:CharSequence\s*\((.*?)\)|String\s*\((.*?)\)|([^\n,]+))", rec)
        big_text_match = re.search(r"android\.bigText=(?:CharSequence\s*\((.*?)\)|String\s*\((.*?)\)|([^\n,]+))", rec)
        sub_text_match = re.search(r"android\.subText=(?:CharSequence\s*\((.*?)\)|String\s*\((.*?)\)|([^\n,]+))", rec)

        title = ""
        if title_match:
            title = next((g for g in title_match.groups() if g is not None), "").strip()

        msg_body = ""
        if big_text_match:
            msg_body = next((g for g in big_text_match.groups() if g is not None), "").strip()
        if not msg_body and text_match:
            msg_body = next((g for g in text_match.groups() if g is not None), "").strip()

        sub_text = ""
        if sub_text_match:
            sub_text = next((g for g in sub_text_match.groups() if g is not None), "").strip()

        # Clean nulls or empty placeholders
        if title in ("null", "None", "") and msg_body in ("null", "None", ""):
            continue

        # Deduplicate
        item = {
            "app": app_name,
            "package": pkg,
            "sender_or_title": title or app_name,
            "message": msg_body or sub_text or "New notification",
            "time_hint": "Recent",
        }

        # Avoid duplicates
        if not any(n["app"] == item["app"] and n["sender_or_title"] == item["sender_or_title"] and n["message"] == item["message"] for n in notifications):
            notifications.append(item)
            if len(notifications) >= max_count:
                break

    return notifications


def read_all_social_and_emails(count: int = 10) -> dict[str, Any]:
    """Combines direct IMAP emails and active phone notifications into a unified report."""
    emails = read_direct_imap_emails(count=count // 2 or 3, unread_only=True)
    phone_notifs = read_phone_notifications(count=count)

    return {
        "ok": True,
        "direct_emails": emails,
        "phone_notifications": phone_notifs,
        "total_items": len(emails) + len(phone_notifs),
    }


def format_messages_report(data: dict[str, Any]) -> str:
    """Formats notifications and messages into an elegant user-facing report."""
    emails = data.get("direct_emails", [])
    notifs = data.get("phone_notifications", [])

    lines = []
    if emails:
        lines.append("📧 RECENT EMAILS (Direct Mailbox):")
        for e in emails:
            lines.append(f"  • From: {e['from']} | Subject: '{e['subject']}'")
        lines.append("")

    if notifs:
        lines.append("📱 RECENT MESSAGES & SOCIAL NOTIFICATIONS (From Phone):")
        for n in notifs:
            lines.append(f"  • [{n['app']}] {n['sender_or_title']}: {n['message']}")
        lines.append("")

    if not lines:
        return (
            "No unread emails or active social media messages found at this moment, Sir.\n"
            "Your notifications are clear, or connect your phone via USB/Wi-Fi to inspect active incoming messages."
        )

    return "\n".join(lines).strip()
