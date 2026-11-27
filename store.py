"""SQLite: Telegram file_id cache (a song sent once is sent again instantly) and per-user limits."""
import sqlite3
import threading
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS files (key TEXT PRIMARY KEY, file_id TEXT, kind TEXT, at REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS uses (user INTEGER, at REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS users (user INTEGER PRIMARY KEY, first REAL, name TEXT)")

    def cached(self, vid, kind):
        with self.lock:
            r = self.db.execute("SELECT file_id FROM files WHERE key=?", (f"{vid}:{kind}",)).fetchone()
        return r[0] if r else None

    def remember(self, vid, kind, file_id):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (f"{vid}:{kind}", file_id, kind, time.time()))

    def forget(self, vid, kind):
        with self.lock, self.db:
            self.db.execute("DELETE FROM files WHERE key=?", (f"{vid}:{kind}",))

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
