"""Direct commands: understood instantly, with NO AI model, so they work offline and never hit a quota.

try_plan(text) returns a Plan (tool steps + a short spoken reply) or None. None means
"not a simple command" and the message goes to the AI brain instead.
"""
from __future__ import annotations

import datetime
import difflib
import re
from dataclasses import dataclass, field
from typing import Callable

try:
    from . import tools
except ImportError:  # direct launch
    import tools  # type: ignore


@dataclass
class Plan:
    steps: list[tuple[str, dict]] = field(default_factory=list)
    say: Callable[[list[str]], str] = lambda results: "Done."
    name: str = ""


# ----------------------------------------------------------------------------- vocabulary
OPEN_VERBS = {"open", "launch", "start", "run", "load", "opne", "opn", "opem", "oppen",
              "افتح", "افتحي", "شغل", "شغّل", "شغلي", "باز", "بکەرەوە", "بکەوە", "هەڵبکە", "بکرەوە"}
PLAY_VERBS = {"play", "playing", "شغل", "شغّل", "پلەی", "لێبدە", "بیلێ"}
FILLER = {"please", "pls", "plz", "can", "could", "you", "would", "jarvis", "hey", "hi", "ok", "okay", "the", "my",
          "to", "now", "for", "me", "a", "an", "app", "application", "program", "جارفيس", "جارڤس", "لطفا", "رجاء", "تكرم",
          "تکایە", "من", "بۆم", "لي", "لی", "ال"}

# canonical app name -> spoken/typed variants (English, Arabic, Kurdish Sorani, common typos)
APP_WORDS = {
    "spotify": ["spotify", "spotfy", "spotifi", "spotyfy", "spotifay", "سبوتيفاي", "سبوتفاي", "سپۆتیفای", "سپۆتیفاى", "سبوتفای"],
    "chrome": ["chrome", "google chrome", "crome", "كروم", "کرۆم", "کروم", "جروم"],
    "edge": ["edge", "microsoft edge", "ايدج", "ئێدج"],
    "firefox": ["firefox", "فايرفوكس", "فایەرفۆکس"],
    "notepad": ["notepad", "note pad", "notes", "المفكرة", "نوتباد", "نۆتپاد"],
    "calculator": ["calculator", "calc", "calculater", "الحاسبة", "الة حاسبة", "حاسبة", "کالکیولەتەر", "ژمێرەر"],
    "paint": ["paint", "mspaint", "الرسام", "پەینت"],
    "file explorer": ["file explorer", "explorer", "files", "my files", "مستكشف الملفات", "ملفات", "فایلەکان"],
    "task manager": ["task manager", "taskmanager", "مدير المهام"],
    "settings": ["settings", "setting", "الاعدادات", "الإعدادات", "اعدادات", "ڕێکخستن", "ڕێکخستنەکان"],
    "vs code": ["vs code", "vscode", "visual studio code", "code editor", "فيجوال ستوديو كود"],
    "whatsapp": ["whatsapp", "whats app", "واتساب", "وەتسئاپ", "واتسئاپ"],
    "telegram": ["telegram", "تلگرام", "تيليجرام", "تێلێگرام"],
    "discord": ["discord", "ديسكورد", "دیسکۆرد"],
    "word": ["word", "ms word", "microsoft word", "ورد"],
    "excel": ["excel", "ms excel", "اكسل", "ئێکسڵ"],
    "powerpoint": ["powerpoint", "power point", "باوربوينت"],
    "camera": ["camera", "كاميرا", "کامێرا"],
    "terminal": ["terminal", "cmd", "command prompt", "powershell", "ترمنال", "تێرمینال"],
    "android studio": ["android studio"],
    "steam": ["steam", "ستيم"],
    "vlc": ["vlc"],
    "zoom": ["zoom", "زوم"],
    "teams": ["teams", "microsoft teams"],
    "snipping tool": ["snipping tool", "snip"],
}
SITE_WORDS = {
    "youtube": ["youtube", "you tube", "yt", "يوتيوب", "یوتووب", "یوتیوب"],
    "google": ["google", "غوغل", "جوجل", "گووگڵ"],
    "gmail": ["gmail", "جيميل", "جیمەیڵ"],
    "github": ["github", "git hub", "جيت هب"],
    "facebook": ["facebook", "فيسبوك", "فەیسبووک"],
    "instagram": ["instagram", "انستغرام", "انستا", "ئینستاگرام"],
    "tiktok": ["tiktok", "tik tok", "تيك توك", "تیکتۆک"],
    "netflix": ["netflix", "نتفلكس"],
    "chatgpt": ["chatgpt", "chat gpt"],
    "gemini": ["gemini"],
    "claude": ["claude"],
    "maps": ["maps", "google maps", "خرائط"],
    "twitter": ["twitter", "x.com"],
    "linkedin": ["linkedin"],
}

ALIAS_TO_APP = {alias: canon for canon, vs in APP_WORDS.items() for alias in vs}
ALIAS_TO_SITE = {alias: canon for canon, vs in SITE_WORDS.items() for alias in vs}
ALL_ALIASES = list(ALIAS_TO_APP) + list(ALIAS_TO_SITE)


# ----------------------------------------------------------------------------- text helpers
def _norm(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s؀-ۿ.:/'+*\/%^()=<>-]", " ", t)      # keep letters, digits, and math operators
    return re.sub(r"\s+", " ", t).strip()


def _lang(text: str) -> str:
    if re.search(r"[ۆەێڕکگھ]|[ڤ]", text):  # Sorani-specific letters
        return "ku"
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    return "en"


PHR = {  # short spoken replies in 3 languages
    "opening": {"en": "Opening {x}.", "ar": "جاري فتح {x}.", "ku": "{x} دەکرێتەوە."},
    "done": {"en": "Done.", "ar": "تم.", "ku": "تەواو."},
    "hello": {"en": "Hello Sir. What can I do for you?", "ar": "أهلاً سيدي. كيف أخدمك؟", "ku": "سڵاو گیان. چۆن یارمەتیت بدەم؟"},
    "thanks": {"en": "Anytime, Sir.", "ar": "على الرحب والسعة.", "ku": "هیچ کێشەیەک نییە."},
}


def _t(key: str, lang: str, **kw) -> str:
    return PHR[key].get(lang, PHR[key]["en"]).format(**kw)


def _strip_filler(words: list[str]) -> list[str]:
    return [w for w in words if w not in FILLER]


def _find_alias(words: list[str]) -> tuple[str, str] | None:
    """Find a known app/site name in the words (tries 3-, 2-, 1-word groups, with typo tolerance)."""
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            if phrase in ALIAS_TO_APP:
                return "app", ALIAS_TO_APP[phrase]
            if phrase in ALIAS_TO_SITE:
                return "site", ALIAS_TO_SITE[phrase]
            if n == 1 and len(phrase) >= 4 and phrase.isascii():
                close = difflib.get_close_matches(phrase, [a for a in ALL_ALIASES if a.isascii() and " " not in a], n=1, cutoff=0.86)
                if close:
                    c = close[0]
                    return ("app", ALIAS_TO_APP[c]) if c in ALIAS_TO_APP else ("site", ALIAS_TO_SITE[c])
    return None


def _result_say(ok_text: Callable[[], str]) -> Callable[[list[str]], str]:
    """Reply with the success text, or the tool's own error message if it failed."""
    def say(results: list[str]) -> str:
        bad = next((r for r in results if tools.failed(r)), None)
        return bad.replace("ERROR: ", "") if bad else ok_text()
    return say


# ----------------------------------------------------------------------------- the single planner
def _try_single_plan(message: str) -> Plan | None:  # noqa: C901 - a flat list of rules is clearest
    raw = message.strip()
    text = _norm(raw)
    if not text or len(text) > 200:
        return None
    lang = _lang(raw)
    words = text.split()
    core = _strip_filler(words) or words

    # ---- greetings / thanks / help
    if text in {"hi", "hello", "hey", "hey jarvis", "hi jarvis", "hello jarvis", "good morning", "good evening", "good afternoon",
                "jarvis", "yo", "سلام", "مرحبا", "اهلا", "أهلا", "هلا", "سڵاو", "سلاو", "چۆنی", "کەیفت چۆنە"}:
        return Plan(say=lambda r: _t("hello", lang), name="greet")
    if re.fullmatch(r"(thanks|thank you|thx|ty|شكرا|شكراً|سوپاس|سپاس|مشكور)( jarvis)?", text):
        return Plan(say=lambda r: _t("thanks", lang), name="thanks")
    if re.fullmatch(r"(help|what can you do|what do you do|commands)", text):
        return Plan(say=lambda r: "I can open apps, control windows (minimize/maximize), run Python code, access your phone, play media, and perform multiple tasks.", name="help")

    # ---- time / date
    if re.search(r"\b(what('?s| is)? the time|time now|what time)\b|^time$|كم الساعة|کاتژمێر", text):
        return Plan(steps=[("get_time", {})], say=lambda r: r[0], name="time")
    if re.search(r"\b(what('?s| is)? (the )?(date|day)|today'?s date)\b|^date$", text):
        return Plan(say=lambda r: datetime.datetime.now().strftime("Today is %A, %d %B %Y."), name="date")

    # ---- media keys & volume
    if re.fullmatch(r"(pause|resume|pause (the )?(music|song|video)|stop (the )?(music|song)|play pause|continue|ايقاف مؤقت|وقف|ڕایبگرە)", text):
        return Plan(steps=[("media_control", {"action": "play_pause"})], say=_result_say(lambda: "Done."), name="playpause")
    if re.fullmatch(r"(next|next song|next track|skip|skip song|skip this|التالي|الاغنية التالية|دواتر)", text):
        return Plan(steps=[("media_control", {"action": "next"})], say=_result_say(lambda: "Next track."), name="next")
    if re.fullmatch(r"(previous|previous song|previous track|prev|go back song|back|السابق|پێشوو)", text):
        return Plan(steps=[("media_control", {"action": "previous"})], say=_result_say(lambda: "Previous track."), name="prev")
    if re.search(r"\b(volume up|louder|turn it up|increase volume|raise volume|turn up)\b|ارفع الصوت|دەنگ زیاد", text):
        n = 15 if re.search(r"\b(a lot|max|much|full)\b", text) else 6
        return Plan(steps=[("media_control", {"action": "volume_up", "times": n})], say=_result_say(lambda: "Volume up."), name="volup")
    if re.search(r"\b(volume down|quieter|lower|turn it down|decrease volume|turn down|softer)\b|اخفض الصوت|دەنگ کەم", text):
        n = 15 if re.search(r"\b(a lot|much|full)\b", text) else 6
        return Plan(steps=[("media_control", {"action": "volume_down", "times": n})], say=_result_say(lambda: "Volume down."), name="voldown")
    if re.search(r"\b(mute|unmute|silence)\b|كتم|بێدەنگ", text):
        return Plan(steps=[("media_control", {"action": "mute"})], say=_result_say(lambda: "Done."), name="mute")

    # ---- window management (minimize, maximize / full size, restore, close)
    m = re.match(r"^(?:please )?(?:minimi[sz]e|صغر|تصغير|بچووک بکەوە)(?:\s+(?:the\s+)?(?:window|screen|نافذة))?(?:\s+(.+))?$", text)
    if m:
        target = (m.group(1) or "").strip()
        if target in ("this", "it", "current", "screen", "active"):
            target = ""
        return Plan(steps=[("minimize_window", {"title": target})],
                    say=_result_say(lambda: f"Minimised {target or 'active window'}."), name="minimize_window")
    m = re.match(r"^(?:please )?(?:maximi[sz]e|full\s*size|fullscreen|كبر|تكبير|ملء الشاشة|گەورە بکەوە)(?:\s+(?:the\s+)?(?:window|screen|نافذة))?(?:\s+(.+))?$", text) \
        or re.match(r"^(?:please )?(?:make (?:it |this )?(?:full\s*size|fullscreen))$", text)
    if m:
        target = (m.group(1) or "").strip() if m.groups() and m.group(1) else ""
        if target in ("this", "it", "current", "screen", "active", "full size", "fullscreen"):
            target = ""
        return Plan(steps=[("maximize_window", {"title": target})],
                    say=_result_say(lambda: f"Maximized {target or 'active window'} to full size."), name="maximize_window")
    m = re.match(r"^(?:please )?(?:restore|رجّع|استعادة)(?:\s+(?:the\s+)?(?:window|نافذة))?(?:\s+(.+))?$", text)
    if m:
        target = (m.group(1) or "").strip()
        return Plan(steps=[("restore_window", {"title": target})],
                    say=_result_say(lambda: f"Restored {target or 'window'} to normal size."), name="restore_window")
    m = re.match(r"^(?:please )?(?:close|اغلق|دابخە)\s+(?:the\s+)?(?:window|this window|نافذة)(?:\s+(.+))?$", text)
    if m:
        target = (m.group(1) or "").strip()
        return Plan(steps=[("close_window", {"title": target})],
                    say=_result_say(lambda: f"Closed {target or 'window'}."), name="close_window")

    # ---- phone & nearby devices
    if re.search(r"\b(?:turn on|switch on|enable|open)\s+(?:the\s+)?(?:phone\s+)?(?:flash\s*light|torch)\b|\b(?:flash\s*light|torch)\s+on\b|شغل الفلاش|ضوء الهاتف", text):
        return Plan(steps=[("toggle_phone_flashlight", {"on": True})],
                    say=_result_say(lambda: "Turned on your phone's flashlight."), name="torch_on")
    if re.search(r"\b(?:turn off|switch off|disable|close)\s+(?:the\s+)?(?:phone\s+)?(?:flash\s*light|torch)\b|\b(?:flash\s*light|torch)\s+off\b|اطفئ الفلاش|إطفاء الفلاش", text):
        return Plan(steps=[("toggle_phone_flashlight", {"on": False})],
                    say=_result_say(lambda: "Turned off your phone's flashlight."), name="torch_off")
    if re.search(r"\b(?:vibrate|buzz)\s+(?:my\s+)?phone\b|هز الهاتف|لەرزین", text):
        return Plan(steps=[("vibrate_phone", {"duration_ms": 600})],
                    say=_result_say(lambda: "Vibrating your phone."), name="vibrate_phone")
    if re.search(r"\b(?:scan network|scan wifi|find all devices|see all devices|scan local network)\b|فحص الشبكة", text):
        return Plan(steps=[("scan_network_devices", {"force": True})],
                    say=lambda r: r[0], name="scan_network")

    m = re.match(r"^(?:please )?(?:unlock|افتح|کردنەوەی)\s+(?:my\s+)?(?:phone|mobile|تلفوني|مۆبایل)(?:\s+(?:with\s+)?(?:pin|pass|passcode)?\s*([0-9a-zA-Z]+))?$", text)
    if m:
        pin = (m.group(1) or "").strip()
        return Plan(steps=[("unlock_phone", {"pin": pin})],
                    say=_result_say(lambda: f"Unlocking phone{' with PIN' if pin else ''}."), name="unlock_phone")
    if re.search(r"\b(wake pc|wake my pc|turn on my pc|wake on lan|wol)\b", text):
        return Plan(steps=[("wake_pc_lan", {})],
                    say=_result_say(lambda: "Sent Wake-on-LAN packet to PC."), name="wake_pc")
    if re.search(r"\b(see pc screen|see screen|view screen|look at pc screen|شاهد الشاشة)\b", text):
        return Plan(steps=[("see_pc_screen", {"question": "Describe current desktop screen"})],
                    say=lambda r: r[0], name="see_screen")
    if re.search(r"\b(see phone screen|view phone screen|mobile screen)\b", text):
        return Plan(steps=[("see_phone_screen", {"question": "Inspect mobile screen"})],
                    say=lambda r: r[0], name="see_phone_screen")
    if re.search(r"\b(start tunnel|start remote tunnel|remote access|enable tunnel)\b", text):
        return Plan(steps=[("start_remote_tunnel", {"provider": "localhost_run"})],
                    say=lambda r: r[0], name="start_tunnel")
    if re.search(r"\b(ring|find|locate)\s+(?:my\s+)?phone\b|where('?s| is) my phone|رن على تلفوني|وين تلفوني|زەنگی مۆبایل", text):
        return Plan(steps=[("ring_phone", {})], say=_result_say(lambda: "Ringing your phone now."), name="ring_phone")
    if re.search(r"\b(phone status|phone battery|how is my phone)\b|بطارية الهاتف", text):
        return Plan(steps=[("get_phone_status", {})], say=lambda r: r[0], name="phone_status")
    if re.search(r"\b(nearby devices|scan devices|find devices)\b|الأجهزة المجاورة", text):
        return Plan(steps=[("list_nearby_devices", {})], say=lambda r: r[0], name="nearby_devices")
    if re.search(r"\b(sync clipboard|send clipboard to phone|copy to phone)\b", text):
        return Plan(steps=[("sync_clipboard_with_phone", {"direction": "to_phone"})],
                    say=_result_say(lambda: "Copied PC clipboard to your phone."), name="sync_clipboard")

    # ---- python execution & math calculation
    m = re.match(r"^(?:run python|python run|eval|exec)\s+(.+)$", raw, flags=re.I | re.S)
    if m:
        return Plan(steps=[("execute_python", {"code": m.group(1)})], say=lambda r: r[0], name="run_python")
    m = re.match(r"^(?:calculate|compute|احسب)\s+([\d\s\+\-\*\/\%\(\)\.\^\*\*]+)$", text)
    if m:
        return Plan(steps=[("execute_python", {"code": f"print({m.group(1)})"})],
                    say=lambda r: f"Result: {r[0]}" if r else "Calculated.", name="calc_python")

    # ---- system actions
    if re.search(r"\b(screenshot|screen shot|take a picture of (the )?screen|screen capture)\b|لقطة شاشة|سکرین شۆت", text):
        return Plan(steps=[("take_screenshot", {})], say=_result_say(lambda: "Screenshot saved in your Pictures folder."), name="shot")
    if re.fullmatch(r"(lock|lock (the )?(pc|computer|screen)|قفل|قفل الشاشة)", text):
        return Plan(steps=[("lock_pc", {})], say=_result_say(lambda: "Locking."), name="lock")
    if re.fullmatch(r"(show desktop|minimi[sz]e all|minimi[sz]e everything|go to desktop|desktop)", text):
        return Plan(steps=[("show_desktop", {})], say=_result_say(lambda: "Done."), name="desktop")
    if re.fullmatch(r"(switch window|next window|alt tab)", text):
        return Plan(steps=[("press_key", {"keys": "alt+tab"})], say=_result_say(lambda: "Switched."), name="alttab")
    if re.fullmatch(r"(new tab|open (a )?new tab)", text):
        return Plan(steps=[("press_key", {"keys": "ctrl+t"})], say=_result_say(lambda: "New tab."), name="newtab")
    if re.fullmatch(r"(close (the )?tab|close this tab)", text):
        return Plan(steps=[("press_key", {"keys": "ctrl+w"})], say=_result_say(lambda: "Tab closed."), name="closetab")
    if re.fullmatch(r"(scroll down|page down)", text):
        return Plan(steps=[("scroll", {"amount": -8})], say=_result_say(lambda: "Scrolled."), name="scrolldown")
    if re.fullmatch(r"(scroll up|page up)", text):
        return Plan(steps=[("scroll", {"amount": 8})], say=_result_say(lambda: "Scrolled."), name="scrollup")
    if re.search(r"\b(system info|system status|cpu|ram|battery|how is my (pc|computer))\b", text) and len(words) <= 6:
        return Plan(steps=[("system_info", {})], say=_result_say(lambda: "Here is your system status."), name="sysinfo")

    # ---- memory
    m = re.match(r"^(?:please )?remember(?: that)? (.+)$", text)
    if m:
        fact = raw.split(None, 2)[-1] if raw.lower().startswith("remember that") else raw.split(None, 1)[-1]
        fact = re.sub(r"^that\s+", "", fact, flags=re.I)
        return Plan(steps=[("remember", {"fact": fact})], say=lambda r: "I will remember that.", name="remember")
    m = re.match(r"^forget (?:about )?(.+)$", text)
    if m:
        return Plan(steps=[("forget", {"keyword": m.group(1)})], say=lambda r: "Forgotten.", name="forget")

    # ---- typing
    m = re.match(r"^(?:type|write)\s+(.+)$", raw, flags=re.I | re.S)
    if m and not re.search(r"\b(email|essay|code|story|letter|poem)\b", text):
        return Plan(steps=[("type_text", {"text": m.group(1)})], say=_result_say(lambda: "Typed."), name="type")

    # ---- search (google / youtube)
    m = re.match(r"^(?:search|look up|find)\s+(?:on |in )?youtube(?: for)?\s+(.+)$", text) \
        or re.match(r"^youtube\s+(?:search\s+)?(?:for\s+)?(.+)$", text) \
        or re.match(r"^(?:open )?youtube and (?:search|play)(?: for)?\s+(.+)$", text) \
        or re.match(r"^(?:search|look up|find)\s+(.+?)\s+(?:on|in) youtube$", text)
    if m:
        q = m.group(1).strip()
        play = bool(re.search(r"\bplay\b", text))
        tool_name = "youtube_play" if play else "youtube_search"
        return Plan(steps=[(tool_name, {"query": q})], say=_result_say(lambda: f"{'Playing' if play else 'Searching YouTube for'} {q}."), name="yt")
    m = re.match(r"^(?:search|find|look up)\s+spotify(?: for)?\s+(.+)$", text)
    if m:
        q = m.group(1).strip()
        return Plan(steps=[("spotify_search", {"query": q})], say=_result_say(lambda: f"Searching Spotify for {q}."), name="spotify")
    m = re.match(r"^(?:google|search google for|search on google for|search for|search the web for|search)\s+(.+)$", text)
    if m and not re.search(r"\b(summari[sz]e|tell me|explain|news|latest|what|who|how|why)\b", text):
        q = m.group(1).strip()
        return Plan(steps=[("open_url", {"url": "https://www.google.com/search?q=" + q.replace(" ", "+")})],
                    say=_result_say(lambda: f"Searching Google for {q}."), name="google")

    # ---- play X [on spotify|youtube]
    m = re.match(r"^(?:play|شغل|شغّل)\s+(.+?)(?:\s+(?:on|in|at)\s+(spotify|youtube|you tube))?$", text)
    if m and m.group(1) not in {"music", "something", "a song", "song", "the music"}:
        q, where = m.group(1).strip(), (m.group(2) or "")
        if where == "spotify" or re.search(r"\bspotify\b", q):
            q = re.sub(r"\s*\bspotify\b\s*", " ", q).strip()
            return Plan(steps=[("spotify_search", {"query": q})], say=_result_say(lambda: f"Searching Spotify for {q}. Press play on the top result."), name="spotify")
        return Plan(steps=[("youtube_play", {"query": q})], say=_result_say(lambda: f"Playing {q}."), name="play")
    m = re.match(r"^(?:search|find)\s+(.+?)\s+(?:on|in)\s+spotify$", text) or re.match(r"^spotify\s+(?:search\s+)?(?:for\s+)?(.+)$", text)
    if m:
        q = m.group(1).strip()
        return Plan(steps=[("spotify_search", {"query": q})], say=_result_say(lambda: f"Searching Spotify for {q}."), name="spotify")

    m = re.match(r"^(?:open|show|go to)\s+(?:my\s+)?(downloads|documents|desktop|pictures|music|videos)(?:\s+folder)?$", text)
    if m:
        folder = m.group(1).title()
        return Plan(steps=[("open_path", {"path": f"~/{folder}"})], say=_result_say(lambda: f"Opening {folder}."), name="folder")

    # ---- open an app / website
    has_open_verb = any(w in OPEN_VERBS for w in words)
    found = _find_alias(core)
    if found and (has_open_verb or len(core) <= 2):
        kind, name = found
        if kind == "site":
            url = tools.SITES.get(name, f"https://www.{name}.com")
            return Plan(steps=[("open_url", {"url": url})], say=_result_say(lambda: _t("opening", lang, x=name.title())), name="open-site")
        return Plan(steps=[("open_app", {"name": name})], say=_result_say(lambda: _t("opening", lang, x=name.title())), name="open-app")
    if has_open_verb:
        rest = " ".join(w for w in core if w not in OPEN_VERBS)
        if rest and len(rest.split()) <= 4:
            return Plan(steps=[("open_app", {"name": rest})], say=_result_say(lambda: _t("opening", lang, x=rest.title())), name="open-any")
    return None


# ----------------------------------------------------------------------------- composite multi-task planner
def try_plan(message: str) -> Plan | None:
    """Understands single commands and multi-task commands ('open Spotify and volume up')."""
    raw = message.strip()
    if not raw or len(raw) > 250:
        return None

    # Check for multi-task compound sentences joined by conjunctions first
    parts = re.split(r"(?:\s+(?:and then|after that|then|and|also|ثم|پاشان|وە)\s+|;\s*|\s+و\s+)", raw, flags=re.I)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) >= 2:
        sub_plans: list[Plan] = []
        for p in parts:
            sp = _try_single_plan(p)
            if sp:
                sub_plans.append(sp)
            else:
                sub_plans = []
                break

        if len(sub_plans) == len(parts):
            combined_steps: list[tuple[str, dict]] = []
            for sp in sub_plans:
                combined_steps.extend(sp.steps)

            def make_composite_say(plans: list[Plan]) -> Callable[[list[str]], str]:
                def say(results: list[str]) -> str:
                    idx = 0
                    phrases: list[str] = []
                    for p in plans:
                        n = len(p.steps)
                        sub_res = results[idx : idx + n]
                        idx += n
                        try:
                            txt = p.say(sub_res)
                            if txt and txt not in ("Done.", "تم.", "تەواو."):
                                phrases.append(txt)
                        except Exception:
                            pass
                    if phrases:
                        return " ".join(phrases)
                    return f"Completed all {len(plans)} tasks."
                return say

            return Plan(steps=combined_steps, say=make_composite_say(sub_plans), name="multi_task")

    # If not multi-task, try single plan
    return _try_single_plan(message)
