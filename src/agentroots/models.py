from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

MAX_RECORD_TITLE_CHARS = 240
MAX_RECORD_BODY_CHARS = 8_000
MAX_RECORD_METADATA_BYTES = 16_384


def validate_record_content(title: str, body: str, metadata: dict[str, Any]) -> None:
    """Enforce the compact governed-record contract at every write boundary."""
    if len(title) > MAX_RECORD_TITLE_CHARS:
        raise ValueError(f"title exceeds {MAX_RECORD_TITLE_CHARS} characters")
    if len(body) > MAX_RECORD_BODY_CHARS:
        raise ValueError(f"body exceeds {MAX_RECORD_BODY_CHARS} characters")
    try:
        metadata_bytes = len(
            json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON serializable") from exc
    if metadata_bytes > MAX_RECORD_METADATA_BYTES:
        raise ValueError(f"metadata exceeds {MAX_RECORD_METADATA_BYTES} UTF-8 bytes")


class RecordType(StrEnum):
    ORIGIN = "origin"
    GOAL = "goal"
    QUESTION = "question"
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    RUN_REF = "run_ref"
    OBSERVATION = "observation"
    CLAIM = "claim"
    FINDING = "finding"
    DECISION = "decision"
    ARTIFACT_REF = "artifact_ref"
    EVIDENCE = "evidence"
    AGENT = "agent"
    SESSION = "session"


class Status(StrEnum):
    CANDIDATE = "candidate"
    PROVISIONAL = "provisional"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"


class Mode(StrEnum):
    PREREGISTERED = "preregistered"
    EXPLORATORY = "exploratory"
    REPLICATION = "replication"
    DEBUGGING = "debugging"


@dataclass(slots=True)
class Record:
    project: str
    type: RecordType
    title: str
    body: str
    creator: str
    mode: Mode = Mode.EXPLORATORY
    status: Status = Status.CANDIDATE
    id: str = field(default_factory=lambda: str(uuid4()))
    revision: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceLink:
    record_id: str
    uri: str
    kind: str
    summary: str = ""
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
