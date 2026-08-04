from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class RecordType(StrEnum):
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
