from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from .private_fs import ensure_private_directory, ensure_private_file

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
  revision INTEGER NOT NULL DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, source_uri TEXT UNIQUE NOT NULL,
  harness TEXT NOT NULL, session_id TEXT NOT NULL, parent_session_id TEXT,
  message_id TEXT NOT NULL, part_ids TEXT NOT NULL, role TEXT NOT NULL,
  text TEXT NOT NULL, content_hash TEXT NOT NULL, metadata TEXT NOT NULL,
  redacted INTEGER NOT NULL DEFAULT 0, injection_risk INTEGER NOT NULL DEFAULT 0,
  source_created_at INTEGER, source_updated_at INTEGER, imported_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
  id UNINDEXED, project UNINDEXED, text, content='episodes', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
 INSERT INTO episodes_fts(rowid,id,project,text) VALUES(new.rowid,new.id,new.project,new.text); END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
 INSERT INTO episodes_fts(episodes_fts,rowid,id,project,text)
 VALUES('delete',old.rowid,old.id,old.project,old.text); END;
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
 INSERT INTO episodes_fts(episodes_fts,rowid,id,project,text)
 VALUES('delete',old.rowid,old.id,old.project,old.text);
 INSERT INTO episodes_fts(rowid,id,project,text) VALUES(new.rowid,new.id,new.project,new.text); END;
CREATE INDEX IF NOT EXISTS idx_episodes_project_time
 ON episodes(project,source_created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_project_hash
 ON episodes(project,content_hash);
CREATE TABLE IF NOT EXISTS hook_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
  project TEXT NOT NULL, session_id TEXT NOT NULL, event_name TEXT NOT NULL,
    query_text TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
    processed_at TEXT, processing_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_hook_events_session
 ON hook_events(project,session_id,id);
CREATE TABLE IF NOT EXISTS hook_injections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
  session_id TEXT NOT NULL, event_name TEXT NOT NULL, query_hash TEXT NOT NULL,
  context_hash TEXT NOT NULL, record_ids TEXT NOT NULL, episode_ids TEXT NOT NULL,
  estimated_tokens INTEGER NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(project,session_id,event_name,query_hash,context_hash)
);
CREATE INDEX IF NOT EXISTS idx_hook_injections_session
 ON hook_injections(project,session_id,id);
CREATE TABLE IF NOT EXISTS extraction_candidates (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, session_id TEXT NOT NULL,
  source_event_id TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, evidence_span TEXT NOT NULL, confidence REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate', metadata TEXT NOT NULL,
  created_at TEXT NOT NULL, resolution_started_at TEXT, resolution_actor TEXT,
  UNIQUE(source_event_id,type,title,evidence_span)
);
CREATE INDEX IF NOT EXISTS idx_extraction_candidates_project
 ON extraction_candidates(project,status,created_at);
CREATE TABLE IF NOT EXISTS hook_notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
  project TEXT NOT NULL, session_id TEXT NOT NULL, kind TEXT NOT NULL,
  message TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
  delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hook_notifications_pending
 ON hook_notifications(project,delivered_at,id);
CREATE TABLE IF NOT EXISTS episode_extraction_audit (
  episode_id TEXT NOT NULL, extractor TEXT NOT NULL, candidate_count INTEGER NOT NULL,
  processed_at TEXT NOT NULL, processing_error TEXT,
  PRIMARY KEY(episode_id,extractor)
);
CREATE TRIGGER IF NOT EXISTS links_require_same_project
BEFORE INSERT ON links
WHEN NOT EXISTS (
  SELECT 1 FROM records source JOIN records target
    ON source.project=target.project
   WHERE source.id=NEW.source_id AND target.id=NEW.target_id
)
BEGIN
  SELECT RAISE(ABORT, 'links require existing records in the same project');
END;
CREATE TRIGGER IF NOT EXISTS evidence_require_record
BEFORE INSERT ON evidence
WHEN NOT EXISTS (SELECT 1 FROM records WHERE id=NEW.record_id)
BEGIN
  SELECT RAISE(ABORT, 'evidence requires an existing record');
END;
CREATE TRIGGER IF NOT EXISTS reviews_require_record
BEFORE INSERT ON reviews
WHEN NOT EXISTS (SELECT 1 FROM records WHERE id=NEW.record_id)
BEGIN
  SELECT RAISE(ABORT, 'reviews require an existing record');
END;
CREATE TRIGGER IF NOT EXISTS events_require_project_record
BEFORE INSERT ON events
WHEN NOT EXISTS (
  SELECT 1 FROM records WHERE id=NEW.record_id AND project=NEW.project
)
BEGIN
  SELECT RAISE(ABORT, 'event project and record must match');
END;
CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reviews_are_append_only_update
BEFORE UPDATE ON reviews
BEGIN
  SELECT RAISE(ABORT, 'reviews are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reviews_are_append_only_delete
BEFORE DELETE ON reviews
BEGIN
  SELECT RAISE(ABORT, 'reviews are append-only');
END;
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        default = user_data_path("agentroots") / "state.sqlite3"
        ensure_private_directory(path.parent, tighten_existing=path == default)
        if not path.exists():
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
            except FileExistsError:
                pass
        self._secure_files()
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(packet_audit)")}
            if "packet_json" not in columns:
                db.execute(
                    "ALTER TABLE packet_audit ADD COLUMN packet_json TEXT NOT NULL DEFAULT '{}'"
                )
            hook_columns = {row[1] for row in db.execute("PRAGMA table_info(hook_events)")}
            if "processing_error" not in hook_columns:
                db.execute("ALTER TABLE hook_events ADD COLUMN processing_error TEXT")
            candidate_columns = {
                row[1] for row in db.execute("PRAGMA table_info(extraction_candidates)")
            }
            if "resolution_started_at" not in candidate_columns:
                db.execute(
                    "ALTER TABLE extraction_candidates ADD COLUMN resolution_started_at TEXT"
                )
            if "resolution_actor" not in candidate_columns:
                db.execute("ALTER TABLE extraction_candidates ADD COLUMN resolution_actor TEXT")
            evidence_columns = {row[1] for row in db.execute("PRAGMA table_info(evidence)")}
            if "revision" not in evidence_columns:
                db.execute(
                    "ALTER TABLE evidence ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )

    def _secure_files(self) -> None:
        for path in (
            self.path,
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
        ):
            ensure_private_file(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        self._secure_files()
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
            self._secure_files()

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in ("metadata", "payload"):
            if key in out and isinstance(out[key], str):
                out[key] = json.loads(out[key])
        return out
