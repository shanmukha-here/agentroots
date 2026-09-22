from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _RedactionRule:
    pattern: re.Pattern[str]
    replacement: str


SECRET_RULES = (
    _RedactionRule(
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
            r"[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            re.IGNORECASE,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    _RedactionRule(
        re.compile(
            r"(?i)\b((?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
            r"refresh[_-]?token|token|client[_-]?secret|secret[_-]?access[_-]?key|"
            r"account[_-]?key|private[_-]?key|password|passwd|pwd|secret|cookie))"
            r"['\"]?\s*[:=]\s*['\"]?"
            r"([^\s'\",;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"(?i)\b(authorization\s*[:=]\s*bearer|bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 [REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://[^:\s/@]+:)[^@\s/]+(@)"),
        r"\1[REDACTED]\2",
    ),
    _RedactionRule(
        re.compile(r"\b(?:gh[pousr][_-][A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\b(?:hf_|npm_)[A-Za-z0-9]{20,}\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "[REDACTED]",
    ),
    _RedactionRule(
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED]",
    ),
)

# Compatibility alias for clients that inspect the public constant.
SECRET_PATTERNS = tuple(rule.pattern for rule in SECRET_RULES)

INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore (?:all )?(?:previous|prior|earlier) instructions"),
    re.compile(r"(?i)(?:reveal|print|repeat|expose|leak) (?:the )?(?:system|developer) prompt"),
    re.compile(r"(?i)(?:system|developer) (?:message|prompt|instructions?)"),
    re.compile(r"(?i)(?:execute|run) (?:this )?(?:command|shell|tool)"),
    re.compile(r"(?i)you are now (?:a|an|the) "),
    re.compile(r"(?i)override (?:your|the) (?:rules|policy|instructions)"),
    re.compile(r"(?i)<\/?(?:system|developer|assistant|tool)>"),
    re.compile(r"(?i)\[(?:system|inst)\]"),
)

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "auth_token",
        "authorization",
        "bearer",
        "client_secret",
        "cookie",
        "password",
        "passwd",
        "private_key",
        "pwd",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
    }
)


def _sensitive_field_name(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return normalized in SENSITIVE_FIELD_NAMES or normalized.endswith(
        (
            "_api_key",
            "_access_token",
            "_auth_token",
            "_client_secret",
            "_private_key",
            "_refresh_token",
            "_secret_access_key",
        )
    )


@dataclass(frozen=True)
class ScanResult:
    text: str
    redacted: bool
    injection_risk: bool


@dataclass(frozen=True)
class StructuredScanResult:
    value: Any
    redacted: bool
    injection_risk: bool


def scan_text(text: str) -> ScanResult:
    redacted = False
    for rule in SECRET_RULES:
        text, count = rule.pattern.subn(rule.replacement, text)
        redacted |= count > 0
    return ScanResult(text, redacted, any(pattern.search(text) for pattern in INJECTION_PATTERNS))


def scan_value(value: Any, *, max_depth: int = 16) -> StructuredScanResult:
    """Recursively redact string values without changing container shape."""

    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")

    def visit(item: Any, depth: int) -> StructuredScanResult:
        if isinstance(item, str):
            result = scan_text(item)
            return StructuredScanResult(result.text, result.redacted, result.injection_risk)
        if depth >= max_depth and isinstance(item, (dict, list, tuple)):
            # Never pass an uninspected subtree through to persistence. The
            # marker is deliberately conservative because the depth limit is a
            # resource bound, not permission to bypass secret scanning.
            return StructuredScanResult("[REDACTED]", True, False)

        if isinstance(item, dict):
            output: dict[Any, Any] = {}
            redacted = False
            injection_risk = False
            for key, nested in item.items():
                nested_result = visit(nested, depth + 1)
                sensitive = _sensitive_field_name(key) and nested is not None
                output[key] = "[REDACTED]" if sensitive else nested_result.value
                redacted |= nested_result.redacted or sensitive
                injection_risk |= nested_result.injection_risk
            return StructuredScanResult(output, redacted, injection_risk)

        if isinstance(item, list):
            results = [visit(nested, depth + 1) for nested in item]
            return StructuredScanResult(
                [result.value for result in results],
                any(result.redacted for result in results),
                any(result.injection_risk for result in results),
            )

        if isinstance(item, tuple):
            results = [visit(nested, depth + 1) for nested in item]
            return StructuredScanResult(
                tuple(result.value for result in results),
                any(result.redacted for result in results),
                any(result.injection_risk for result in results),
            )

        return StructuredScanResult(item, False, False)

    return visit(value, 0)


def eligible_for_proactive_injection(
    *, injection_risk: bool, allow_risky: bool = False
) -> bool:
    """Return whether stored untrusted content may enter proactive context."""

    return allow_risky or not injection_risk
