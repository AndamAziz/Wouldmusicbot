"""SQLite: link keys (Telegram buttons hold only 64 bytes), file_id cache (a file sent once is sent again
instantly), per-user settings and limits."""
import hashlib
import sqlite3
import threading
import time

DEFAULTS = {"mode": "ask", "aq": "a320", "vq": "v720"}
MODES = ("ask", "audio", "video")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS files (key TEXT PRIMARY KEY, file_id TEXT, kind TEXT, at REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS uses (user INTEGER, at REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS users (user INTEGER PRIMARY KEY, first REAL, name TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS links (key TEXT PRIMARY KEY, url TEXT, title TEXT, at REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS settings (user INTEGER PRIMARY KEY, mode TEXT, aq TEXT, vq TEXT)")

    # ---- links: short key <-> url
    def link(self, url, title=""):
        key = hashlib.sha1(url.encode()).hexdigest()[:12]
        with self.lock, self.db:
            self.db.execute("INSERT INTO links VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET at=excluded.at, "
                            "title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE links.title END",
                            (key, url, title, time.time()))
        return key

    def url(self, key):
        with self.lock:
            r = self.db.execute("SELECT url, title FROM links WHERE key=?", (key,)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    # ---- file_id cache
    def cached(self, key, kind):
        with self.lock:
            r = self.db.execute("SELECT file_id FROM files WHERE key=?", (f"{key}:{kind}",)).fetchone()
        return r[0] if r else None

    def remember(self, key, kind, file_id):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (f"{key}:{kind}", file_id, kind, time.time()))

    def forget(self, key, kind):
        with self.lock, self.db:
            self.db.execute("DELETE FROM files WHERE key=?", (f"{key}:{kind}",))

    # ---- settings
    def settings(self, user):
        with self.lock:
            r = self.db.execute("SELECT mode, aq, vq FROM settings WHERE user=?", (user,)).fetchone()
        return dict(zip(("mode", "aq", "vq"), r)) if r else dict(DEFAULTS)

    def set(self, user, field, value):
        s = self.settings(user)
        s[field] = value
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?,?,?)", (user, s["mode"], s["aq"], s["vq"]))
        return s

    # ---- limits / users
    def used_last_hour(self, user, now=None):
        now = now or time.time()
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM uses WHERE user=? AND at>?", (user, now - 3600)).fetchone()[0]

    def use(self, user, now=None):
        now = now or time.time()
        with self.lock, self.db:
            self.db.execute("INSERT INTO uses VALUES (?,?)", (user, now))
            self.db.execute("DELETE FROM uses WHERE at<?", (now - 86400,))

    def seen(self, user, name):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO users VALUES (?,?,?)", (user, time.time(), name))

    def stats(self):
        with self.lock:
            q = lambda s: self.db.execute(s).fetchone()[0]
            return {"users": q("SELECT COUNT(*) FROM users"), "cached": q("SELECT COUNT(*) FROM files"),
                    "last24h": q("SELECT COUNT(*) FROM uses")}
