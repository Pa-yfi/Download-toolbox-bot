"""SQLite persistence (WAL mode = crash-safe, concurrent-friendly).

Replaces the fragile whole-file JSON store. Survives restarts: languages,
pending states, live button data, delivered-file cache, stats, bans, quotas.
"""
import json
import sqlite3
import threading
import time
from config import DB_FILE

_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA synchronous=NORMAL")
_lock = threading.Lock()

_conn.executescript("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, lang TEXT, banned INTEGER DEFAULT 0,
    day TEXT DEFAULT '', count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS files(key TEXT PRIMARY KEY, file_id TEXT);
CREATE TABLE IF NOT EXISTS cache(
    key TEXT PRIMARY KEY, value TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS pending(
    chat INTEGER, user INTEGER, state TEXT, PRIMARY KEY(chat, user));
CREATE TABLE IF NOT EXISTS stats(k TEXT PRIMARY KEY, v INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
""")
_conn.commit()

CACHE_TTL = 6 * 3600  # live buttons stay valid 6h, across restarts


def _exec(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur


# ---------------- users ----------------
def get_lang(uid: int) -> str | None:
    row = _exec("SELECT lang FROM users WHERE id=?", (uid,)).fetchone()
    return row[0] if row and row[0] else None


def set_lang(uid: int, lang: str):
    _exec("INSERT INTO users(id, lang) VALUES(?,?) "
          "ON CONFLICT(id) DO UPDATE SET lang=?", (uid, lang, lang))


def is_banned(uid: int) -> bool:
    row = _exec("SELECT banned FROM users WHERE id=?", (uid,)).fetchone()
    return bool(row and row[0])


def set_ban(uid: int, banned: bool):
    _exec("INSERT INTO users(id, banned) VALUES(?,?) "
          "ON CONFLICT(id) DO UPDATE SET banned=?",
          (uid, int(banned), int(banned)))


def quota_ok(uid: int, limit: int) -> bool:
    """Read-only check: does the user still have quota today?
    Does NOT consume — failed/cancelled downloads must not cost quota."""
    today = time.strftime("%Y-%m-%d")
    row = _exec("SELECT day, count FROM users WHERE id=?", (uid,)).fetchone()
    if not row or row[0] != today:
        return True
    return row[1] < limit


def quota_use(uid: int):
    """Consume one unit — call ONLY after a successful delivery."""
    today = time.strftime("%Y-%m-%d")
    row = _exec("SELECT day, count FROM users WHERE id=?", (uid,)).fetchone()
    count = row[1] if row and row[0] == today else 0
    _exec("INSERT INTO users(id, day, count) VALUES(?,?,?) "
          "ON CONFLICT(id) DO UPDATE SET day=?, count=?",
          (uid, today, count + 1, today, count + 1))


def ensure_bot_identity(bot_id: int):
    """file_ids are only valid for the token that uploaded them. If the bot
    identity changed (token revoked/replaced), the whole cache is stale —
    clear it so users never receive dead cached results."""
    row = _exec("SELECT v FROM meta WHERE k='bot_id'").fetchone()
    if row and row[0] != str(bot_id):
        _exec("DELETE FROM files")
    _exec("INSERT OR REPLACE INTO meta VALUES('bot_id', ?)", (str(bot_id),))


def all_user_ids() -> list[int]:
    return [r[0] for r in _exec("SELECT id FROM users").fetchall()]


# ---------------- pending states ----------------
def pending_get(chat: int, user: int) -> str | None:
    row = _exec("SELECT state FROM pending WHERE chat=? AND user=?",
                (chat, user)).fetchone()
    return row[0] if row else None


def pending_set(chat: int, user: int, state: str):
    _exec("INSERT OR REPLACE INTO pending VALUES(?,?,?)", (chat, user, state))


def pending_pop(chat: int, user: int):
    _exec("DELETE FROM pending WHERE chat=? AND user=?", (chat, user))


# ---------------- generic cache (URL / search results) ----------------
def cache_put(key: str, value):
    _exec("INSERT OR REPLACE INTO cache VALUES(?,?,?)",
          (key, json.dumps(value, ensure_ascii=False), time.time()))
    _exec("DELETE FROM cache WHERE ts < ?", (time.time() - CACHE_TTL,))


def cache_get(key: str):
    row = _exec("SELECT value, ts FROM cache WHERE key=?", (key,)).fetchone()
    if not row or time.time() - row[1] > CACHE_TTL:
        return None
    return json.loads(row[0])


def cache_pop(key: str):
    v = cache_get(key)
    _exec("DELETE FROM cache WHERE key=?", (key,))
    return v


# ---------------- delivered-file cache ----------------
def get_cached_file(key: str) -> str | None:
    row = _exec("SELECT file_id FROM files WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_cached_file(key: str, file_id: str):
    _exec("INSERT OR REPLACE INTO files VALUES(?,?)", (key, file_id))


# ---------------- stats ----------------
def stat_inc(k: str, n: int = 1):
    _exec("INSERT INTO stats(k, v) VALUES(?,?) "
          "ON CONFLICT(k) DO UPDATE SET v=v+?", (k, n, n))


def stats_all() -> dict:
    return dict(_exec("SELECT k, v FROM stats").fetchall())
