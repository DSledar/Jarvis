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
    "vs code": ["vs code", "vscode", "visual studio code", "code editor", "فيجوال ستوديو كود", "ڤی ئێس کۆد", "ڤی ئێس کۆدە", "کۆد ئێدیتەر"],
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
    if re.search(r"[ۆەێڕڵکگھڤ]|(?:بکەرەوە|بکەوە|دابخە|لێبدە|دەنگ|سوپاس|سڵاو|چۆنی|چۆنیت|گیان|تکایە|ئەمڕۆ|کاتژمێر|سەعات|پەنجەرە|مۆبایل|خەو|قوفڵ)", text):
        return "ku"
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    return "en"


PHR = {  # short spoken replies in 3 languages
    "opening": {"en": "Opening {x}.", "ar": "جاري فتح {x}.", "ku": "بەسەرچاو گەورەم، دەستبەجێ {x} دەکرێتەوە."},
    "done": {"en": "Done.", "ar": "تم.", "ku": "فەرمانەکەت جێبەجێ کرا گەورەم."},
    "hello": {"en": "Hello Sir. What can I do for you?", "ar": "أهلاً سيدي. كيف أخدمك؟", "ku": "سڵاو گەورەم. چۆن دەتوانم یارمەتیت بدەم؟"},
    "thanks": {"en": "Anytime, Sir.", "ar": "على الرحب والسعة.", "ku": "شایەنی نییە گەورەم، هەمیشە لە خزمەتدام."},
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
    if re.search(r"\b(wake up jarvis|wake up|jarvis wake up|wake up buddy|wake jarvis|boot up jarvis)\b|هەڵسە جارڤیس|بەخەبەر وەرە|استيقظ جارفيس|استيقظ", text):
        return Plan(say=lambda r: "بەخێربێیتەوە گەورەم، هەموو سیستەمەکان ئامادەن و گوێت لێ دەگرم." if lang == "ku" else "Welcome back, Sir. All systems initialized and online.", name="wake_jarvis")
    if re.search(r"\b(goodbye jarvis|goodby jarvis|good bye jarvis|go to sleep jarvis|go to sleep|sleep jarvis|sleep mode|bye jarvis)\b|خوات لەگەڵ جارڤیس|خوات لەگەڵ|بخەوە جارڤیس|تەواو بخەوە|دەتوانیت بخەویت|الى اللقاء جارفيس|نم جارفيس", text):
        return Plan(say=lambda r: "دەچمە دۆخی خەوتنەوە گەورەم. هەر کاتێک وتت هەڵسە جارڤیس، دەگەڕێمەوە خزمەتت." if lang == "ku" else "Entering sleep mode, Sir. Call me whenever you need me.", name="sleep_jarvis")
    if re.search(r"\b(open camera|start camera|live cam|camera vision|look at this|see this|webcam)\b|کامێرا بکەرەوە|سەیری ئەمە بکە|افتح الكاميرا", text):
        return Plan(say=lambda r: "کامێرای ڕاستەوخۆ کرایەوە، فەرموو سەیری دەکەم و گوێم لێتە." if lang == "ku" else "Opening Live Camera Vision. I am watching and listening.", name="open_camera")

    if text in {"hi", "hello", "hey", "hey jarvis", "hi jarvis", "hello jarvis", "good morning", "good evening", "good afternoon",
                "jarvis", "yo", "سلام", "مرحبا", "اهلا", "أهلا", "هلا", "سڵاو", "سلاو", "چۆنی", "چۆنیت", "باشی", "کەیفت چۆنە", "چۆنی گیان", "سڵاو لە تۆ", "بەیانیت باش", "ئێوارەت باش"}:
        return Plan(say=lambda r: _t("hello", lang), name="greet")
    if re.fullmatch(r"(thanks|thank you|thx|ty|شكرا|شكراً|سوپاس|سووپاس|دەستت خۆش|دەستخۆش|زۆر سوپاس|سپاس|مشكور)( jarvis| جارڤیس)?", text):
        return Plan(say=lambda r: _t("thanks", lang), name="thanks")
    if re.fullmatch(r"(help|what can you do|what do you do|commands|یارمەتی|چی دەتوانی بکەی)", text):
        return Plan(say=lambda r: "دەتوانم بەرنامەکان بکەمەوە، کۆنترۆڵی پەنجەرەکان بکەم، کۆد بنووسم، پەیوەندی بە مۆبایلەکەتەوە بکەم، و کارەکان بەخێرایی ئەنجام بدەم." if lang == "ku" else "I can open apps, control windows (minimize/maximize), run Python code, access your phone, play media, and perform multiple tasks.", name="help")

    # ---- time / date
    if re.search(r"\b(what('?s| is)? the time|time now|what time)\b|^time$|كم الساعة|کاتژمێر|سەعات|سەعات چەندە|کاتژمێر چەندە", text):
        return Plan(steps=[("get_time", {})], say=lambda r: r[0], name="time")
    if re.search(r"\b(what('?s| is)? (the )?(date|day)|today'?s date)\b|^date$|ئەمڕۆ چەندە|بەروار|ئەمڕۆ چ ڕۆژێکە", text):
        return Plan(say=lambda r: datetime.datetime.now().strftime("Today is %A, %d %B %Y."), name="date")

    # ---- media keys & volume
    if re.fullmatch(r"(pause|resume|pause (the )?(music|song|video)|stop (the )?(music|song)|play pause|continue|ايقاف مؤقت|وقف|ڕایبگرە|بوەستێنە|ڕاگرتن)", text):
        return Plan(steps=[("media_control", {"action": "play_pause"})], say=_result_say(lambda: "تەواو، ڕاگیرا." if lang == "ku" else "Done."), name="playpause")
    if re.fullmatch(r"(next|next song|next track|skip|skip song|skip this|التالي|الاغنية التالية|دواتر|گۆرانی دواتر|دواتری لێبدە)", text):
        return Plan(steps=[("media_control", {"action": "next"})], say=_result_say(lambda: "گۆرانی دواتر." if lang == "ku" else "Next track."), name="next")
    if re.fullmatch(r"(previous|previous song|previous track|prev|go back song|back|السابق|پێشوو|گۆرانی پێشوو)", text):
        return Plan(steps=[("media_control", {"action": "previous"})], say=_result_say(lambda: "گۆرانی پێشوو." if lang == "ku" else "Previous track."), name="prev")
    if re.search(r"\b(volume up|louder|turn it up|increase volume|raise volume|turn up)\b|ارفع الصوت|دەنگ زیاد|دەنگ بەرز|دەنگ زۆر|دەنگی بەرز|دەنگەکە بەرز بکەوە", text):
        n = 15 if re.search(r"\b(a lot|max|much|full)\b", text) else 6
        return Plan(steps=[("media_control", {"action": "volume_up", "times": n})], say=_result_say(lambda: "دەنگ بەرز کرایەوە." if lang == "ku" else "Volume up."), name="volup")
    if re.search(r"\b(volume down|quieter|lower|turn it down|decrease volume|turn down|softer)\b|اخفض الصوت|دەنگ کەم|دەنگ نزم|دەنگ هێواش|دەنگەکە کەم بکەوە", text):
        n = 15 if re.search(r"\b(a lot|much|full)\b", text) else 6
        return Plan(steps=[("media_control", {"action": "volume_down", "times": n})], say=_result_say(lambda: "دەنگ کەم کرایەوە." if lang == "ku" else "Volume down."), name="voldown")
    if re.search(r"\b(mute|unmute|silence)\b|كتم|بێدەنگ|دەنگ ببڕە|بێدەنگی بکە", text):
        return Plan(steps=[("media_control", {"action": "mute"})], say=_result_say(lambda: "بێدەنگ کرا." if lang == "ku" else "Done."), name="mute")

    # ---- window management (minimize, maximize / full size, restore, close)
    m = re.match(r"^(?:please )?(?:minimi[sz]e|صغر|تصغير|بچووک بکەوە|پەنجەرە بچووک بکەوە|بچووکی بکەوە)(?:\s+(?:the\s+)?(?:window|screen|نافذة|پەنجەرە))?(?:\s+(.+))?$", text)
    if m:
        target = (m.group(1) or "").strip()
        if target in ("this", "it", "current", "screen", "active", "پەنجەرە", "ئەمە"):
            target = ""
        return Plan(steps=[("minimize_window", {"title": target})],
                    say=_result_say(lambda: f"پەنجەرەی {target or 'دیاریکراو'} بچووک کرایەوە." if lang == "ku" else f"Minimised {target or 'active window'}."), name="minimize_window")
    m = re.match(r"^(?:please )?(?:maximi[sz]e|full\s*size|fullscreen|كبر|تكبير|ملء الشاشة|گەورە بکەوە|پەنجەرە گەورە بکەوە|گەورەی بکە|پڕ بە شاشە)(?:\s+(?:the\s+)?(?:window|screen|نافذة|پەنجەرە))?(?:\s+(.+))?$", text) \
        or re.match(r"^(?:please )?(?:make (?:it |this )?(?:full\s*size|fullscreen))$", text)
    if m:
        target = (m.group(1) or "").strip() if m.groups() and m.group(1) else ""
        if target in ("this", "it", "current", "screen", "active", "full size", "fullscreen", "پەنجەرە"):
            target = ""
        return Plan(steps=[("maximize_window", {"title": target})],
                    say=_result_say(lambda: f"پەنجەرەی {target or 'دیاریکراو'} پڕ بە شاشە گەورە کرایەوە." if lang == "ku" else f"Maximized {target or 'active window'} to full size."), name="maximize_window")
    m = re.match(r"^(?:please )?(?:restore|رجّع|استعادة|گەڕاندنەوە)(?:\s+(?:the\s+)?(?:window|نافذة|پەنجەرە))?(?:\s+(.+))?$", text)
    if m:
        target = (m.group(1) or "").strip()
        return Plan(steps=[("restore_window", {"title": target})],
                    say=_result_say(lambda: f"Restored {target or 'window'} to normal size."), name="restore_window")
    m = re.match(r"^(?:please )?(?:close|اغلق|دابخە|پەنجەرە دابخە|دایبخە)\s+(?:the\s+)?(?:window|this window|نافذة|پەنجەرە)(?:\s+(.+))?$", text)
    if m:
        target = (m.group(1) or "").strip()
        return Plan(steps=[("close_window", {"title": target})],
                    say=_result_say(lambda: f"پەنجەرەی {target or 'دیاریکراو'} داخرا." if lang == "ku" else f"Closed {target or 'window'}."), name="close_window")

    # ---- phone & nearby devices
    if re.search(r"\b(?:turn on|switch on|enable|open)\s+(?:the\s+)?(?:phone\s+)?(?:flash\s*light|torch)\b|\b(?:flash\s*light|torch)\s+on\b|شغل الفلاش|ضوء الهاتف|فلاش پێبکە|فلاش دابگیرسێنە|فلاشی مۆبایل پێبکە|فلاش لێبدە|لایت پێبکە", text):
        return Plan(steps=[("toggle_phone_flashlight", {"on": True})],
                    say=_result_say(lambda: "فلاشی مۆبایلەکەت داگیرسا." if lang == "ku" else "Turned on your phone's flashlight."), name="torch_on")
    if re.search(r"\b(?:turn off|switch off|disable|close)\s+(?:the\s+)?(?:phone\s+)?(?:flash\s*light|torch)\b|\b(?:flash\s*light|torch)\s+off\b|اطفئ الفلاش|إطفاء الفلاش|فلاش بکوژێنەوە|فلاش بکوژینەوە|فلاشی مۆبایل بکوژێنەوە|لایت بکوژێنەوە", text):
        return Plan(steps=[("toggle_phone_flashlight", {"on": False})],
                    say=_result_say(lambda: "فلاشی مۆبایلەکەت کوژایەوە." if lang == "ku" else "Turned off your phone's flashlight."), name="torch_off")
    if re.search(r"\b(?:vibrate|buzz)\s+(?:my\s+)?phone\b|هز الهاتف|لەرزین|مۆبایل بلەرزێنە|لەرزین بۆ مۆبایل|هەزی مۆبایل", text):
        return Plan(steps=[("vibrate_phone", {"duration_ms": 600})],
                    say=_result_say(lambda: "مۆبایلەکەت لەرزی." if lang == "ku" else "Vibrating your phone."), name="vibrate_phone")
    if re.search(r"\b(?:scan network|scan wifi|find all devices|see all devices|scan local network)\b|فحص الشبكة|فەحسی نێتۆڕک بکە|ئامێرەکان بدۆزەوە|ئامێرەکانی نێتۆڕک", text):
        return Plan(steps=[("scan_network_devices", {"force": True})],
                    say=lambda r: r[0], name="scan_network")

    # ------------------------------------------------------------------------- Phone Calls (V6)
    call_m = re.search(r"\b(?:call|phone|dial|make a call to)\s+([\+0-9\s\-]{4,20})\b|تەلەفۆن بکە بۆ\s+([\+0-9\s\-]{4,20})|پەیوەندی بکە بە\s+([\+0-9\s\-]{4,20})|اتصل بـ?\s*([\+0-9\s\-]{4,20})", text)
    if call_m:
        raw_num = next((g for g in call_m.groups() if g is not None), "").strip()
        num = "".join(c for c in raw_num if c.isdigit() or c in ("+", "*", "#"))
        if num:
            return Plan(steps=[("make_phone_call", {"phone_number": num, "direct": True})],
                        say=_result_say(lambda: f"پەیوەندی دەبەسترێت بە {num} لە ڕێگەی مۆبایلەکەتەوە." if lang == "ku" else f"Placing call to {num} via your phone."), name="make_phone_call")

    if re.search(r"\b(?:hang up|end call|disconnect call|stop call)\b|تەلەفۆنەکە دابخەوە|پەیوەندی ببڕە|پەیوەندی کۆتایی پێبێنە|اغلق المكالمة|انهي الاتصال", text):
        return Plan(steps=[("end_phone_call", {})],
                    say=_result_say(lambda: "پەیوەندییەکە بڕدرا." if lang == "ku" else "Call ended."), name="end_phone_call")

    if re.search(r"\b(?:answer call|pick up phone|accept call)\b|وەڵامی تەلەفۆن بدەوە|تەلەفۆن هەڵگرە|رد على المكالمة|اجب على الاتصال", text):
        return Plan(steps=[("answer_phone_call", {})],
                    say=_result_say(lambda: "وەڵامی پەیوەندی درایەوە." if lang == "ku" else "Answering call."), name="answer_phone_call")

    # ------------------------------------------------------------------------- Social Media, Messages & Emails (V6)
    if re.search(r"\b(?:read|check|see|inspect|get|show)\s+(?:my\s+)?(?:all\s+|new\s+|received\s+|incoming\s+)?(?:messages|texts|whatsapp|telegram|instagram|social|notifications|dms)\b|نامەکانم بخوێنەوە|نامەکان بخوێنەوە|نامەم بۆ بخوێنەوە|سەیری نامە بکە|نامەی نوێ هەیە|سەیری واتساپ بکە|سەیری تێلیگرام بکە|نۆتیفیکەیشن|ئاگادارییەکان|اقرأ رسائلي|اقرأ الرسائل|شوف الرسايل|افحص الإشعارات|رسائل الواتساب", text):
        return Plan(steps=[("read_social_messages", {"count": 10})], say=lambda r: r[0], name="read_social_messages")

    if re.search(r"\b(?:read|check|get|show)\s+(?:my\s+)?(?:all\s+|new\s+|unread\s+|received\s+)?(?:emails|mail|inbox|gmail)\b|ئیمەیڵەکانم بخوێنەوە|ئیمەیڵ بخوێنەوە|سەیری ئیمەیڵ بکە|ئیمەیڵی نوێ هەیە|نامەی ئیمەیڵ|اقرأ الإيميلات|اقرأ البريد|شوف البريد", text):
        return Plan(steps=[("read_emails", {"count": 5, "unread_only": True})], say=lambda r: r[0], name="read_emails")

    # ------------------------------------------------------------------------- Always-On Screen Awareness (V6)
    if re.search(r"\b(?:what is|what's|see|view|look at|describe|explain)\s+(?:on\s+|at\s+)?(?:the\s+|my\s+)?(?:pc\s+|laptop\s+)?screen\b|چی لەسەر شاشەکەمە|چی لەسەر شاشەیە|سەیری شاشەکەم بکە|سەیری شاشە بکە|ئەمەی سەر شاشەم بۆ بخوێنەوە|شاشەکەم چی تێدایە|ماذا يوجد على شاشتي|شوف شاشتي|اقرأ الشاشة|شاهد الشاشة", text):
        return Plan(steps=[("always_see_pc_screen", {})], say=lambda r: r[0], name="always_see_pc_screen")

    # ------------------------------------------------------------------------- Laptop Brightness & Power Control (V6)
    bright_set_m = re.search(r"\b(?:set\s+)?brightness\s+(?:to\s+)?(\d{1,3})\s*%?\b", text)
    if bright_set_m:
        val = int(bright_set_m.group(1))
        return Plan(steps=[("set_screen_brightness", {"percent": val})],
                    say=_result_say(lambda: f"ڕووناکی شاشە خرایە سەر {val}%." if lang == "ku" else f"Screen brightness set to {val}%."), name="set_brightness")

    if re.search(r"\b(?:increase brightness|brightness up)\b|ڕووناکی شاشە زیاد بکە|رفع إضاءة الشاشة", text):
        return Plan(steps=[("set_screen_brightness", {"percent": 90})],
                    say=_result_say(lambda: "ڕووناکی شاشە بەرز کرایەوە." if lang == "ku" else "Screen brightness increased."), name="brightness_up")

    if re.search(r"\b(?:decrease brightness|brightness down|dim screen)\b|ڕووناکی شاشە کەم بکەوە|تقليل إضاءة الشاشة", text):
        return Plan(steps=[("set_screen_brightness", {"percent": 40})],
                    say=_result_say(lambda: "ڕووناکی شاشە کەم کرایەوە." if lang == "ku" else "Screen brightness dimmed."), name="brightness_down")

    if re.search(r"\b(?:laptop battery|battery health|battery percentage|power status)\b|پاتری کۆمپیوتەر|پاتری لاپتۆپ|شحن اللابتوب", text):
        return Plan(steps=[("battery_telemetry", {})], say=lambda r: r[0], name="battery_telemetry")

    # Phone unlock (with optional PIN or automatic remembered PIN)
    pin_m = re.search(r"\b(?:pin|pass|passcode|کۆدی|کۆد)?\s*([0-9]{4,8})\b", text)
    pin_found = pin_m.group(1) if pin_m else ""
    if re.search(r"\b(?:unlock|open)\s+(?:my\s+)?(?:phone|mobile|screen)\b|\b(?:افتح|فتح)\s+(?:الهاتف|الموبايل|الجوال|تلفوني)\b|مۆبایلەکەم بکەوە|مۆبایلم بکەوە|مۆبایل بکەوە|مۆبایلەکە بکەوە|کردنەوەی مۆبایل|تەلەفۆنەکەم بکەوە|تەلەفۆن بکەوە|بێ کۆد مۆبایل", text):
        return Plan(steps=[("unlock_phone", {"pin": pin_found})],
                    say=_result_say(lambda: f"مۆبایلەکەت دەکرێتەوە{' بە کۆد' if pin_found else ' بە کۆدی یادگاری'}." if lang == "ku" else f"Unlocking phone{' with PIN' if pin_found else ' with remembered PIN'}."), name="unlock_phone")

    if re.search(r"\b(?:switch to wireless|switch phone to wifi|wireless adb|unplug phone|phone wifi mode)\b|مۆبایل بخە سەر وایفای|مۆبایلم بخە سەر وایفای|وایفای مۆبایل|وایەر لە مۆبایل دەربێنە", text):
        return Plan(steps=[("switch_phone_to_wifi", {})],
                    say=_result_say(lambda: "مۆبایلەکەت خرایە دۆخی وایفای، ئێستا دەتوانیت وایەرەکە لێ بکەیتەوە." if lang == "ku" else "Switched phone to wireless Wi-Fi mode. You can now unplug the cable."), name="switch_phone_to_wifi")

    if re.search(r"\b(wake pc|wake my pc|turn on my pc|wake on lan|wol)\b|شغل الكمبيوتر|کۆمپیوتەر پێبکە|کۆمپیوتەر هەڵسێنە|کۆمپیوتەر وەخەبەر بێنە", text):
        return Plan(steps=[("wake_pc_lan", {})],
                    say=_result_say(lambda: "سیگناڵی هەڵساندنی کۆمپیوتەر نێردرا." if lang == "ku" else "Sent Wake-on-LAN packet to PC."), name="wake_pc")
    if re.search(r"\b(sleep pc|put pc to sleep|sleep computer)\b|نوم الكمبيوتر|کۆمپیوتەر بخە خەو|خەوتنی کۆمپیوتەر|بیخە خەو", text):
        return Plan(steps=[("sleep_pc", {}) if "sleep_pc" in tools.TOOLS else ("press_key", {"keys": "sleep"})],
                    say=_result_say(lambda: "کۆمپیوتەر خرایە دۆخی خەوەوە." if lang == "ku" else "Putting PC to sleep."), name="sleep_pc")
    if re.search(r"\b(see pc screen|see screen|view screen|look at pc screen|شاهد الشاشة|شاشە ببینە)\b", text):
        return Plan(steps=[("see_pc_screen", {"question": "Describe current desktop screen"})],
                    say=lambda r: r[0], name="see_screen")
    if re.search(r"\b(see phone screen|view phone screen|mobile screen|شاشەی مۆبایل ببینە)\b", text):
        return Plan(steps=[("see_phone_screen", {"question": "Inspect mobile screen"})],
                    say=lambda r: r[0], name="see_phone_screen")
    if re.search(r"\b(start tunnel|start remote tunnel|remote access|enable tunnel)\b", text):
        return Plan(steps=[("start_remote_tunnel", {"provider": "localhost_run"})],
                    say=lambda r: r[0], name="start_tunnel")
    if re.search(r"\b(ring|find|locate)\s+(?:my\s+)?phone\b|where('?s| is) my phone|رن على تلفوني|وين تلفوني|زەنگی مۆبایل|مۆبایلم بدۆزەوە|زەنگ لێبدە بۆ مۆبایلم|مۆبایلەکەم بدۆزەوە|زەنگ لێبدە بۆ تەلەفۆنەکەم", text):
        return Plan(steps=[("ring_phone", {})], say=_result_say(lambda: "دەستبەجێ زەنگ لە مۆبایلەکەت دەدرێت تا بیدۆزیتەوە." if lang == "ku" else "Ringing your phone now."), name="ring_phone")
    if re.search(r"\b(phone status|phone battery|how is my phone)\b|بطارية الهاتف|پاتری مۆبایل|مۆبایلم چۆنە", text):
        return Plan(steps=[("get_phone_status", {})], say=lambda r: r[0], name="phone_status")
    if re.search(r"\b(nearby devices|scan devices|find devices)\b|الأجهزة المجاورة|ئامێرەکان بدۆزەوە", text):
        return Plan(steps=[("list_nearby_devices", {})], say=lambda r: r[0], name="nearby_devices")
    if re.search(r"\b(sync clipboard|send clipboard to phone|copy to phone)\b|کلیپبۆرد بۆ مۆبایل", text):
        return Plan(steps=[("sync_clipboard_with_phone", {"direction": "to_phone"})],
                    say=_result_say(lambda: "کلیپبۆرد کۆپی کرا بۆ مۆبایلەکەت." if lang == "ku" else "Copied PC clipboard to your phone."), name="sync_clipboard")

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
