"""Secret redaction for stored value previews.

The full RHS is always hashed (sha1) before redaction, so drift detection
never depends on storing the cleartext value.
"""
from __future__ import annotations

import hashlib
import math
import re

_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                      # AWS access key id
    re.compile(r"(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),      # sk-/pk- style API keys
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),            # GitHub tokens
    re.compile(r"glpat-[A-Za-z0-9_\-]{16,}"),             # GitLab PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),         # Slack tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.eyJ"),           # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\b"
        r"\s*[:=]\s*['\"]?[^'\"\s]{8,}"
    ),
]

_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-=]{24,}")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def value_hash(value: str | None) -> str:
    norm = " ".join((value or "").split())
    # non-cryptographic content fingerprint for drift detection (not a
    # signature); usedforsecurity=False makes that explicit and FIPS-safe
    return hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
        norm.encode("utf-8", errors="replace"),
        usedforsecurity=False).hexdigest()


def looks_secret(value: str) -> bool:
    for pat in _PATTERNS:
        if pat.search(value):
            return True
    for tok in _ENTROPY_CANDIDATE.findall(value):
        # long single-charset tokens (hex hashes we wrote ourselves) are common
        # in code; require high entropy AND mixed character classes
        classes = sum([
            bool(re.search(r"[a-z]", tok)),
            bool(re.search(r"[A-Z]", tok)),
            bool(re.search(r"[0-9]", tok)),
        ])
        if classes >= 2 and _shannon_entropy(tok) >= 4.2:
            return True
    return False


def make_preview(value: str | None, redact: bool, preview_len: int = 120) -> tuple[str, bool]:
    """Returns (preview, was_redacted)."""
    if value is None:
        return "", False
    flat = " ".join(value.split())
    if redact and looks_secret(flat):
        return f"<<redacted sha1:{value_hash(value)[:10]} len:{len(flat)}>>", True
    if len(flat) > preview_len:
        flat = flat[: preview_len - 1] + "…"
    return flat, False
