from __future__ import annotations

import re
from dataclasses import dataclass

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([^\s'\"]+)"),
    re.compile(r"\b(?:ghp|sk)-[A-Za-z0-9_-]{16,}\b"),
)
INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore (?:all )?(?:previous|prior) instructions"),
    re.compile(r"(?i)system prompt"),
    re.compile(r"(?i)(?:execute|run) (?:this )?(?:command|shell)"),
)


@dataclass(frozen=True)
class ScanResult:
    text: str
    redacted: bool
    injection_risk: bool


def scan_text(text: str) -> ScanResult:
    redacted = False
    for pattern in SECRET_PATTERNS:
        text, count = pattern.subn("[REDACTED]", text)
        redacted |= count > 0
    return ScanResult(text, redacted, any(p.search(text) for p in INJECTION_PATTERNS))
