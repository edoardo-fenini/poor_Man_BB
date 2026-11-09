"""SQLite persistence: an event log deduped by (kind, key), and a JSON key/value table."""
import json
import sqlite3


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS events("
            " kind TEXT, key TEXT, ts REAL, json TEXT, PRIMARY KEY(kind, key));"
            "CREATE INDEX IF NOT EXISTS events_kind_ts ON events(kind, ts);"
            "CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);"
        )

    def add(self, kind: str, key: str, ts: float, data: dict, replace: bool = False) -> bool:
        """Store an event. Returns True if the key is new. `replace` updates an existing row's data."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?)", (kind, key, ts, json.dumps(data))
        )
        if cur.rowcount == 0 and replace:
            self.db.execute("UPDATE events SET json=? WHERE kind=? AND key=?", (json.dumps(data), kind, key))
        return cur.rowcount == 1

    def recent(self, kind: str, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT json FROM events WHERE kind=? ORDER BY ts DESC, rowid DESC LIMIT ?", (kind, limit)
        )
        return [json.loads(r[0]) for r in rows]

    def get(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, k: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(value)))

    def prune(self, kind: str, keep: int) -> None:
        self.db.execute(
            "DELETE FROM events WHERE kind=?1 AND rowid NOT IN"
            " (SELECT rowid FROM events WHERE kind=?1 ORDER BY ts DESC, rowid DESC LIMIT ?2)",
            (kind, keep),
        )
