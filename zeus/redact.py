"""Strip secrets and personal data before anything leaves the machine.

Everything sent to a model provider passes through `redact()`. The patterns
are deliberately conservative: a false positive costs a little context, a
false negative leaks a credential.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("gcp_key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("gcp_token", re.compile(r"\bya29\.[A-Za-z0-9_-]+\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("uri_credentials", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("secret_assignment", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s;,]+)")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card_number", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def redact(text: str) -> str:
    """Return `text` with sensitive values replaced by [REDACTED:<kind>]."""
    for kind, pattern in _PATTERNS:
        if kind == "secret_assignment":
            text = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED:secret]", text)
        else:
            text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text
