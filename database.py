"""
Database layer for Emina. Minimal by design: users, memories, short-term
conversation history. Nothing here backs a feature that doesn't exist yet —
add tables when you add the handler that uses them, not before.
"""
import os
import time
import aiosqlite

from config import DB_PATH, logger

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at REAL,
    updated_at REAL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL
);
"""

_db: aiosqlite.Connection | None = None


async def init_db():
    global _db
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.commit()
    logger.info("Database ready at %s", DB_PATH)
    return _db


def db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() at startup.")
    return _db


# ---------- Users ----------

async def upsert_user(user_id: int, username: str | None, first_name: str | None):
    await db().execute(
        """INSERT INTO users (user_id, username, first_name, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                first_name=excluded.first_name""",
        (user_id, username, first_name, time.time()),
    )
    await db().commit()


# ---------- Memories ----------

async def add_memory(user_id: int, content: str) -> int:
    now = time.time()
    cur = await db().execute(
        "INSERT INTO memories (user_id, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (user_id, content, now, now),
    )
    await db().commit()
    return cur.lastrowid


async def list_memories(user_id: int, limit: int = 40):
    cur = await db().execute(
        "SELECT * FROM memories WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    )
    return await cur.fetchall()


async def search_memories(user_id: int, query: str):
    cur = await db().execute(
        "SELECT * FROM memories WHERE user_id=? AND content LIKE ? ORDER BY updated_at DESC",
        (user_id, f"%{query}%"),
    )
    return await cur.fetchall()


async def delete_memory(user_id: int, memory_id: int) -> bool:
    cur = await db().execute(
        "DELETE FROM memories WHERE user_id=? AND memory_id=?", (user_id, memory_id)
    )
    await db().commit()
    return cur.rowcount > 0


async def edit_memory(user_id: int, memory_id: int, new_content: str) -> bool:
    cur = await db().execute(
        "UPDATE memories SET content=?, updated_at=? WHERE user_id=? AND memory_id=?",
        (new_content, time.time(), user_id, memory_id),
    )
    await db().commit()
    return cur.rowcount > 0


async def memory_exists_like(user_id: int, content: str) -> bool:
    """Cheap duplicate guard before inserting an extracted fact."""
    cur = await db().execute(
        "SELECT 1 FROM memories WHERE user_id=? AND content=? LIMIT 1", (user_id, content)
    )
    return (await cur.fetchone()) is not None


# ---------- Conversation history (short-term memory) ----------

async def add_message(chat_id: int, user_id: int, role: str, content: str):
    await db().execute(
        "INSERT INTO conversation_history (chat_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (chat_id, user_id, role, content, time.time()),
    )
    await db().commit()


async def recent_messages(chat_id: int, limit: int = 12):
    cur = await db().execute(
        "SELECT role, content FROM conversation_history WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = await cur.fetchall()
    return list(reversed(rows))
