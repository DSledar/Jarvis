"""SQLite memory: multi-session persistent chat history + auto-learning knowledge engine."""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "jarvis_memory.sqlite3"
HISTORY_TURNS = int(os.getenv("JARVIS_HISTORY_TURNS", "20"))


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Table: messages
    con.execute(
        """CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL DEFAULT 0
        )"""
    )

    # Table: sessions (for browsing, switching, and managing chat history)
    con.execute(
        """CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at REAL,
            updated_at REAL,
            turn_count INTEGER DEFAULT 0
        )"""
    )

    # Table: facts (long-term memory about user, habits, preferences)
    con.execute(
        """CREATE TABLE IF NOT EXISTS facts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT UNIQUE,
            category TEXT DEFAULT 'general',
            learned_at REAL DEFAULT 0,
            hits INTEGER DEFAULT 1
        )"""
    )

    # Table: learned_knowledge (structured key/value memory)
    con.execute(
        """CREATE TABLE IF NOT EXISTS learned_knowledge(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            value TEXT,
            context TEXT,
            updated_at REAL
        )"""
    )

    # Check and migrate columns if missing
    cur = con.cursor()
    msg_cols = [r["name"] for r in cur.execute("PRAGMA table_info(messages)").fetchall()]
    if "timestamp" not in msg_cols:
        try:
            con.execute("ALTER TABLE messages ADD COLUMN timestamp REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    fact_cols = [r["name"] for r in cur.execute("PRAGMA table_info(facts)").fetchall()]
    if "category" not in fact_cols:
        try:
            con.execute("ALTER TABLE facts ADD COLUMN category TEXT DEFAULT 'general'")
        except sqlite3.OperationalError:
            pass
    if "learned_at" not in fact_cols:
        try:
            con.execute("ALTER TABLE facts ADD COLUMN learned_at REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
    if "hits" not in fact_cols:
        try:
            con.execute("ALTER TABLE facts ADD COLUMN hits INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass

    # Backfill sessions table from existing messages if needed
    try:
        session_count = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if session_count == 0:
            distinct_sessions = con.execute("SELECT DISTINCT session FROM messages").fetchall()
            now = time.time()
            for row in distinct_sessions:
                s_id = row[0]
                first_msg = con.execute(
                    "SELECT content FROM messages WHERE session=? AND role='user' ORDER BY id ASC LIMIT 1",
                    (s_id,)
                ).fetchone()
                msg_cnt = con.execute("SELECT COUNT(*) FROM messages WHERE session=?", (s_id,)).fetchone()[0]
                title = (first_msg[0][:48] + "...") if first_msg and len(first_msg[0]) > 48 else (first_msg[0] if first_msg else f"Chat {s_id[:8]}")
                title = title.replace("\n", " ").strip() or f"Chat {s_id[:8]}"
                con.execute(
                    "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at, turn_count) VALUES (?,?,?,?,?)",
                    (s_id, title, now, now, msg_cnt // 2)
                )
    except Exception:
        pass

    return con


def _safe(session_id: str) -> str:
    cleaned = "".join(c for c in session_id if c.isalnum() or c in "_-.")[:80]
    return cleaned or "default"


# --------------------------------------------------------------------------- chat history
def load_history(session_id: str, limit: int | None = None) -> list[dict]:
    turns = limit or HISTORY_TURNS
    safe_id = _safe(session_id)
    with _db() as con:
        rows = con.execute(
            "SELECT role, content FROM messages WHERE session=? ORDER BY id DESC LIMIT ?",
            (safe_id, turns * 2),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_turn(session_id: str, user: str, assistant: str) -> None:
    safe_id = _safe(session_id)
    now = time.time()

    with _db() as con:
        # Save messages
        con.executemany(
            "INSERT INTO messages(session, role, content, timestamp) VALUES (?,?,?,?)",
            [(safe_id, "user", user, now), (safe_id, "assistant", assistant, now)],
        )

        # Update or create session record
        existing = con.execute("SELECT id, title, turn_count FROM sessions WHERE id=?", (safe_id,)).fetchone()
        if not existing:
            first_title = user.strip().split("\n")[0][:48]
            if len(user.strip().split("\n")[0]) > 48:
                first_title += "..."
            first_title = first_title.strip() or f"Session {safe_id[:8]}"
            con.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at, turn_count) VALUES (?,?,?,?,1)",
                (safe_id, first_title, now, now),
            )
        else:
            con.execute(
                "UPDATE sessions SET updated_at=?, turn_count=turn_count+1 WHERE id=?",
                (now, safe_id),
            )

    # Automatically learn preferences, facts, and habits from this interaction
    try:
        auto_learn_from_interaction(user, assistant)
    except Exception:
        pass


def get_sessions() -> list[dict[str, Any]]:
    """Returns list of all chat sessions for browsing and switching."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, title, created_at, updated_at, turn_count FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"] or f"Chat {r['id'][:8]}",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "turn_count": r["turn_count"],
            })
        return result


def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Returns all messages in a session for chat history viewing."""
    safe_id = _safe(session_id)
    with _db() as con:
        rows = con.execute(
            "SELECT id, role, content, timestamp FROM messages WHERE session=? ORDER BY id ASC",
            (safe_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "timestamp": r["timestamp"] or 0,
            }
            for r in rows
        ]


def create_session(title: str = "") -> dict[str, Any]:
    """Creates a new conversation session."""
    now = time.time()
    new_id = f"sess_{int(now)}_{os.urandom(3).hex()}"
    final_title = title.strip() or f"New Chat {time.strftime('%b %d %H:%M')}"
    with _db() as con:
        con.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at, turn_count) VALUES (?,?,?,?,0)",
            (new_id, final_title, now, now),
        )
    return {"id": new_id, "title": final_title, "created_at": now, "turn_count": 0}


def delete_session(session_id: str) -> bool:
    safe_id = _safe(session_id)
    with _db() as con:
        con.execute("DELETE FROM messages WHERE session=?", (safe_id,))
        con.execute("DELETE FROM sessions WHERE id=?", (safe_id,))
    return True


def clear_history(session_id: str) -> None:
    safe_id = _safe(session_id)
    with _db() as con:
        con.execute("DELETE FROM messages WHERE session=?", (safe_id,))
        con.execute("UPDATE sessions SET turn_count=0, updated_at=? WHERE id=?", (time.time(), safe_id))


def search_history(query: str) -> dict[str, Any]:
    """Searches across messages and long-term facts for a keyword or phrase."""
    q = query.strip()
    if not q:
        return {"messages": [], "facts": []}

    like = f"%{q}%"
    with _db() as con:
        msg_rows = con.execute(
            """SELECT m.id, m.session, m.role, m.content, m.timestamp, s.title
               FROM messages m
               LEFT JOIN sessions s ON m.session = s.id
               WHERE m.content LIKE ?
               ORDER BY m.id DESC LIMIT 30""",
            (like,),
        ).fetchall()

        fact_rows = con.execute(
            "SELECT id, fact, category, learned_at FROM facts WHERE fact LIKE ? ORDER BY id DESC LIMIT 20",
            (like,),
        ).fetchall()

    messages = [
        {
            "id": r["id"],
            "session": r["session"],
            "session_title": r["title"] or r["session"],
            "role": r["role"],
            "content": r["content"],
            "timestamp": r["timestamp"],
        }
        for r in msg_rows
    ]

    facts = [
        {
            "id": r["id"],
            "fact": r["fact"],
            "category": r["category"],
            "learned_at": r["learned_at"],
        }
        for r in fact_rows
    ]

    return {"query": q, "messages": messages, "facts": facts}


# --------------------------------------------------------------------------- long-term facts & auto-learning
def add_fact(fact: str, category: str = "general") -> str:
    fact = fact.strip()[:400]
    if not fact:
        return "Nothing to remember."
    now = time.time()
    with _db() as con:
        # Check if already exists; if so, bump hits
        existing = con.execute("SELECT id, hits FROM facts WHERE fact=?", (fact,)).fetchone()
        if existing:
            con.execute("UPDATE facts SET hits=hits+1 WHERE id=?", (existing["id"],))
            return f"Already remembered: {fact}"
        con.execute(
            "INSERT OR IGNORE INTO facts(fact, category, learned_at, hits) VALUES (?,?,?,1)",
            (fact, category, now),
        )
    return f"Remembered: {fact}"


def list_facts(category: str | None = None) -> list[str]:
    with _db() as con:
        if category:
            rows = con.execute(
                "SELECT fact FROM facts WHERE category=? ORDER BY hits DESC, id DESC LIMIT 50",
                (category,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT fact FROM facts ORDER BY hits DESC, id DESC LIMIT 50"
            ).fetchall()
        return [r["fact"] for r in rows]


def list_detailed_facts() -> list[dict[str, Any]]:
    with _db() as con:
        rows = con.execute(
            "SELECT id, fact, category, learned_at, hits FROM facts ORDER BY hits DESC, id DESC LIMIT 100"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "fact": r["fact"],
                "category": r["category"] or "general",
                "learned_at": r["learned_at"] or 0,
                "hits": r["hits"] or 1,
            }
            for r in rows
        ]


def remove_facts(keyword: str) -> str:
    kw = keyword.strip()
    if not kw:
        return "No keyword specified."
    with _db() as con:
        n = con.execute("DELETE FROM facts WHERE fact LIKE ?", (f"%{kw}%",)).rowcount
    return f"Forgot {n} memory item(s)."


# --------------------------------------------------------------------------- continuous auto-learning
LEARN_PATTERNS = [
    # User identity
    (r"(?:my name is|i am called|call me)\s+([A-Za-z0-9_\-\s]{2,40})", "User name is {0}", "identity"),
    # Preferences
    (r"(?:i prefer|i always prefer|i like to use|my preferred)\s+([^\.,!\n]{3,80})", "User prefers {0}", "preference"),
    (r"(?:i love|i really like|my favorite\s+\w+\s+is)\s+([^\.,!\n]{3,80})", "User likes {0}", "preference"),
    (r"(?:i don't like|i hate|never use|don't ever)\s+([^\.,!\n]{3,80})", "User dislikes {0}", "preference"),
    # Devices & Hardware
    (r"(?:my phone is|my mobile is|i have an?)\s+(iphone[^\.,!\n]*|android[^\.,!\n]*|samsung[^\.,!\n]*|xiaomi[^\.,!\n]*|pixel[^\.,!\n]*)", "User device: {0}", "hardware"),
    (r"(?:my pc has|my graphics card is|my gpu is|my cpu is)\s+([^\.,!\n]{3,80})", "PC Specs: {0}", "hardware"),
    # Projects & Coding
    (r"(?:i am working on|my project is|we are building)\s+([^\.,!\n]{3,100})", "Current project: {0}", "project"),
    (r"(?:i code in|i program in|my main language is)\s+([A-Za-z0-9#\+\s]{2,40})", "User codes in {0}", "coding"),
]


def auto_learn_from_interaction(user_msg: str, assistant_msg: str) -> list[str]:
    """Analyzes conversation turn to automatically learn and persist facts about the user."""
    learned = []
    text = user_msg.strip()

    for pattern, template, category in LEARN_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            captured = match.group(1).strip()
            # Clean trailing punctuation
            captured = re.sub(r"[\.,;!]+$", "", captured)
            if captured and len(captured) >= 2:
                fact = template.format(captured)
                add_fact(fact, category)
                learned.append(fact)

    # Detect explicit "remember that ..." or "note that ..."
    explicit_match = re.search(r"(?:remember that|note that|keep in mind that)\s+([^\n!]{5,200})", text, re.IGNORECASE)
    if explicit_match:
        fact = explicit_match.group(1).strip()
        add_fact(fact, "user_note")
        learned.append(fact)

    return learned


def recall_relevant_context(query: str, max_items: int = 5) -> str:
    """Finds memories, facts, and past context most relevant to the current user query."""
    q = query.lower()
    words = [w for w in re.findall(r"\w+", q) if len(w) > 2]
    if not words:
        return ""

    matched_facts = []
    with _db() as con:
        # Search facts
        for row in con.execute("SELECT fact, hits FROM facts ORDER BY hits DESC LIMIT 100").fetchall():
            fact_txt = row["fact"]
            f_low = fact_txt.lower()
            score = sum(1 for w in words if w in f_low)
            if score > 0:
                matched_facts.append((score + (row["hits"] * 0.1), fact_txt))

    matched_facts.sort(key=lambda x: x[0], reverse=True)
    top_facts = [f[1] for f in matched_facts[:max_items]]

    if not top_facts:
        return ""

    lines = ["RELEVANT USER CONTEXT & MEMORY:"]
    for f in top_facts:
        lines.append(f"- {f}")
    return "\n".join(lines)
