from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
  project TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER NOT NULL,
  event_type TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
  payload TEXT NOT NULL, idempotency_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS records (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, creator TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
  revision INTEGER NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}', UNIQUE(source_id,target_id,relation)
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, uri TEXT NOT NULL, kind TEXT NOT NULL,
  summary TEXT NOT NULL, content_hash TEXT, metadata TEXT NOT NULL,
  UNIQUE(record_id, uri)
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, actor TEXT NOT NULL, verdict TEXT NOT NULL,
  comment TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packet_audit (
  packet_id TEXT PRIMARY KEY, project TEXT NOT NULL, query TEXT NOT NULL,
  record_ids TEXT NOT NULL, packet_hash TEXT NOT NULL, created_at TEXT NOT NULL,
  used_record_ids TEXT, packet_json TEXT NOT NULL DEFAULT '{}'
);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(id UNINDEXED,title,body,content='records',content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
 INSERT INTO records_fts(rowid,id,title,body) VALUES(new.rowid,new.id,new.title,new.body); END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
 INSERT INTO records_fts(records_fts,rowid,id,title,body) VALUES('delete',old.rowid,old.id,old.title,old.body); END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
 INSERT INTO records_fts(records_fts,rowid,id,title,body) VALUES('delete',old.rowid,old.id,old.title,old.body);
 INSERT INTO records_fts(rowid,id,title,body) VALUES(new.rowid,new.id,new.title,new.body); END;
CREATE INDEX IF NOT EXISTS idx_records_project ON records(project,status,type);
CREATE INDEX IF NOT EXISTS idx_events_record ON events(record_id,revision);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(packet_audit)")}
            if "packet_json" not in columns:
                db.execute(
                    "ALTER TABLE packet_audit ADD COLUMN packet_json TEXT NOT NULL DEFAULT '{}'"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in ("metadata", "payload"):
            if key in out and isinstance(out[key], str):
                out[key] = json.loads(out[key])
        return out
