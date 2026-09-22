from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from .models import EvidenceLink

VerificationStatus = Literal[
    "verified", "reference_valid", "asserted", "unverified", "missing", "stale", "invalid"
]

TERMINAL_RUN_STATES = {"FINISHED", "FAILED", "KILLED"}
EPISTEMIC_RECORD_TYPES = {"claim", "finding", "observation", "decision"}
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV = re.compile(r"^(?:arxiv:)?\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
MAX_LOCAL_EVIDENCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class EvidenceVerification:
    status: VerificationStatus
    method: str
    reason: str
    content_hash: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def to_metadata(self) -> dict[str, Any]:
        metadata = {
            "status": self.status,
            "method": self.method,
            "reason": self.reason,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        if self.content_hash is not None:
            metadata["resolved_content_hash"] = self.content_hash
        return metadata


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hashable_file(path: Path) -> EvidenceVerification | None:
    try:
        size = path.stat().st_size
    except OSError:
        return EvidenceVerification("missing", "file", "referenced file does not exist")
    if size > MAX_LOCAL_EVIDENCE_BYTES:
        return EvidenceVerification(
            "unverified",
            "file",
            f"local evidence exceeds the {MAX_LOCAL_EVIDENCE_BYTES}-byte verification limit",
        )
    return None


def _inside(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    candidate = candidate.resolve()
    return candidate == root or root in candidate.parents


def _local_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    value = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        return None
    if re.match(r"^/[A-Za-z]:/", value):
        value = value[1:]
    return Path(value)


def verify_evidence(
    conn: sqlite3.Connection,
    *,
    project: str,
    link: EvidenceLink,
    project_root: Path | None = None,
    state_root: Path | None = None,
    trusted_tracker: bool = False,
) -> EvidenceVerification:
    """Validate an evidence reference without executing stored commands or code."""

    uri = link.uri.strip()
    kind = link.kind.strip().lower()
    metadata = link.metadata
    if not uri:
        return EvidenceVerification("invalid", "syntax", "empty evidence URI")

    if kind == "episode-span":
        episode_id = str(metadata.get("episode_id", ""))
        span = str(metadata.get("evidence_span", "")).strip()
        if not episode_id or not span:
            return EvidenceVerification(
                "invalid", "episode-span", "episode_id and evidence_span are required"
            )
        row = conn.execute(
            "SELECT project,text,injection_risk FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        if row is None or row["project"] != project:
            return EvidenceVerification("missing", "episode-span", "source episode not found")
        if span not in str(row["text"]):
            return EvidenceVerification(
                "stale", "episode-span", "evidence span no longer matches the source episode"
            )
        digest = hashlib.sha256(span.encode("utf-8")).hexdigest()
        if link.content_hash and link.content_hash != digest:
            return EvidenceVerification("stale", "episode-span", "span hash mismatch", digest)
        if row["injection_risk"]:
            return EvidenceVerification(
                "unverified",
                "episode-span",
                "source episode is flagged for prompt-injection risk",
                digest,
            )
        return EvidenceVerification(
            "reference_valid",
            "episode-span",
            "exact span matched an untrusted conversation source",
            digest,
        )

    if kind == "git-file":
        if project_root is None:
            return EvidenceVerification(
                "unverified", "git-file", "project_root is required for local verification"
            )
        root = project_root.resolve()
        path = (root / uri).resolve()
        if not _inside(root, path):
            return EvidenceVerification("invalid", "git-file", "path escapes project root")
        if not path.is_file():
            return EvidenceVerification("missing", "git-file", "referenced file does not exist")
        bounded = _hashable_file(path)
        if bounded is not None:
            return EvidenceVerification(bounded.status, "git-file", bounded.reason)
        digest = _sha256(path)
        if link.content_hash and link.content_hash != digest:
            return EvidenceVerification("stale", "git-file", "file hash mismatch", digest)
        return EvidenceVerification("verified", "git-file", "file exists and hash matched", digest)

    if kind in {"file", "artifact", "artifact-file"}:
        local_path = _local_path(uri)
        if local_path is None:
            return EvidenceVerification("reference_valid", "uri", "non-local artifact reference")
        allowed_roots = [root.resolve() for root in (project_root, state_root) if root is not None]
        if not allowed_roots or not any(_inside(root, local_path) for root in allowed_roots):
            return EvidenceVerification("invalid", "file", "local file is outside trusted roots")
        if not local_path.is_file():
            return EvidenceVerification("missing", "file", "referenced file does not exist")
        bounded = _hashable_file(local_path)
        if bounded is not None:
            return bounded
        digest = _sha256(local_path)
        if link.content_hash and link.content_hash != digest:
            return EvidenceVerification("stale", "file", "file hash mismatch", digest)
        if not link.content_hash:
            return EvidenceVerification(
                "verified",
                "file",
                "file exists; SHA-256 recorded at link time",
                digest,
            )
        return EvidenceVerification("verified", "file", "file exists and hash matched", digest)

    if kind in {"tracker-run", "mlflow-run", "trackio-run"}:
        status = str(metadata.get("external_status", "")).upper()
        run_id = str(metadata.get("run_id", ""))
        if not run_id or status not in TERMINAL_RUN_STATES or not link.content_hash:
            return EvidenceVerification(
                "unverified",
                "tracker-run",
                "terminal run_id, status, and provenance hash are required",
            )
        if not _HEX_SHA256.fullmatch(link.content_hash):
            return EvidenceVerification("invalid", "tracker-run", "invalid provenance hash")
        if not trusted_tracker:
            return EvidenceVerification(
                "reference_valid",
                "tracker-run",
                "tracker snapshot requires live adapter validation on this installation",
            )
        return EvidenceVerification(
            "verified", "tracker-run", "live adapter resolved a terminal tracker snapshot"
        )

    if kind in {"test", "test-result", "test-receipt"}:
        exit_code = metadata.get("exit_code")
        command = str(metadata.get("command", "")).strip()
        trace_uri = str(metadata.get("trace_uri", ""))
        trace_path = _local_path(trace_uri)
        if (
            not isinstance(exit_code, int)
            or not command
            or not link.content_hash
            or trace_path is None
        ):
            return EvidenceVerification(
                "unverified",
                "test-receipt",
                "exit_code, command, local trace_uri, and trace hash are required",
            )
        if not _HEX_SHA256.fullmatch(link.content_hash):
            return EvidenceVerification("invalid", "test-receipt", "invalid receipt hash")
        allowed_roots = [root.resolve() for root in (project_root, state_root) if root is not None]
        if not allowed_roots or not any(_inside(root, trace_path) for root in allowed_roots):
            return EvidenceVerification(
                "invalid", "test-receipt", "test receipt is outside trusted roots"
            )
        if not trace_path.is_file():
            return EvidenceVerification("missing", "test-receipt", "test trace does not exist")
        bounded = _hashable_file(trace_path)
        if bounded is not None:
            return EvidenceVerification(bounded.status, "test-receipt", bounded.reason)
        trace_hash = _sha256(trace_path)
        if link.content_hash != trace_hash:
            return EvidenceVerification(
                "stale", "test-receipt", "test trace hash mismatch", trace_hash
            )
        try:
            receipt = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return EvidenceVerification(
                "unverified", "test-receipt", "test receipt is not valid JSON"
            )
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema") != "agentroots.test-receipt.v1"
            or receipt.get("command") != command
            or receipt.get("exit_code") != exit_code
        ):
            return EvidenceVerification(
                "unverified",
                "test-receipt",
                "test receipt does not bind the declared command and exit code",
            )
        return EvidenceVerification(
            "reference_valid",
            "test-receipt",
            (
                "hashed structured receipt matched its declared command and exit code, "
                "but no trusted runner attested the result"
            ),
            trace_hash,
        )

    if kind in {"doi", "paper"}:
        identifier = uri.removeprefix("doi:").removeprefix("https://doi.org/")
        if _DOI.fullmatch(identifier):
            return EvidenceVerification("reference_valid", "doi", "well-formed DOI reference")
        return EvidenceVerification("invalid", "doi", "malformed DOI reference")

    if kind == "arxiv":
        identifier = uri.removeprefix("arxiv:").rsplit("/", 1)[-1]
        if _ARXIV.fullmatch(identifier):
            return EvidenceVerification("reference_valid", "arxiv", "well-formed arXiv reference")
        return EvidenceVerification("invalid", "arxiv", "malformed arXiv reference")

    if kind in {"human", "human-statement"}:
        return EvidenceVerification("asserted", "human", "human statement is not mechanically verified")

    parsed = urlparse(uri)
    if parsed.scheme:
        return EvidenceVerification(
            "reference_valid", "uri", "URI is well formed but was not mechanically resolved"
        )
    return EvidenceVerification("invalid", "syntax", "evidence requires a supported URI or kind")


def verified_evidence_present(rows: list[sqlite3.Row]) -> bool:
    for row in rows:
        metadata = json.loads(row["metadata"])
        if metadata.get("verification", {}).get("status") == "verified":
            return True
    return False
