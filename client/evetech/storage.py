"""Local database. Each operation owns and closes its SQLite connection."""

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .model import Report


@dataclass
class Record:
    id: int
    initial: Report
    latest: Report
    recovery: str


def data_directory():
    base = os.environ.get("EVETECH_DATA_DIR") or os.environ.get("CLASSFIX_DATA_DIR")
    if base:
        return Path(base)
    parent = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    current = parent / "EveTech"
    legacy = parent / "ClassFix"
    if not (current / "evetech.sqlite3").exists() and (legacy / "classfix.sqlite3").is_file():
        return legacy
    return current


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "evetech.sqlite3"
        legacy = directory / "classfix.sqlite3"
        if not self.path.exists() and legacy.is_file():
            self.path = legacy
        with self.open() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS diagnosis (
                         id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                         initial TEXT NOT NULL, latest TEXT NOT NULL, recovery TEXT NOT NULL)""")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @contextmanager
    def open(self):
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            yield db

    def save(self, initial, latest, recovery, record_id=None):
        first = json.dumps(initial.to_dict(), ensure_ascii=False)
        last = json.dumps(latest.to_dict(), ensure_ascii=False)
        with self.open() as db:
            if record_id is not None:
                cursor = db.execute("UPDATE diagnosis SET latest=?, recovery=? WHERE id=?",
                                    (last, recovery, record_id))
                if cursor.rowcount:
                    return record_id
            cursor = db.execute("INSERT INTO diagnosis(kind,initial,latest,recovery) VALUES (?,?,?,?)",
                                (latest.kind, first, last, recovery))
            return cursor.lastrowid

    def records(self, limit=150, kind=None):
        with self.open() as db:
            if kind is None:
                rows = db.execute("SELECT id,initial,latest,recovery FROM diagnosis ORDER BY id DESC LIMIT ?", (limit,))
            else:
                rows = db.execute("SELECT id,initial,latest,recovery FROM diagnosis WHERE kind=? ORDER BY id DESC LIMIT ?",
                                  (kind, limit))
            return [Record(key, Report.from_dict(json.loads(first)), Report.from_dict(json.loads(last)), recovered)
                    for key, first, last, recovered in rows]

    def settings(self):
        with self.open() as db:
            return dict(db.execute("SELECT key,value FROM settings"))

    def update_settings(self, values):
        with self.open() as db:
            db.executemany("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           values.items())
