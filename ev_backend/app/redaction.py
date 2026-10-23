"""Best-effort PII redaction for complaint and review text.

Redacts (in order):
  - email addresses
  - phone numbers (Indian +10 digit, optionally +91 prefixed; also spaced forms)
  - vehicle registration numbers (e.g. MH-12-AB-1234, DL01AB1234, KA 01 AB 1234)
  - card-like digit sequences (13-19 digits, possibly grouped with spaces/dashes)
  - a small set of obvious name patterns like "My name is X Y"

This is BEST-EFFORT ONLY — it is not a guarantee that no personal data remains.
Documented in the README as required.
"""
from __future__ import annotations

import re
from typing import List, Tuple

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){13,18}\d(?!\d)")
PHONE_RE = re.compile(r"(?:(?:\+|00)?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
# Vehicle registration: two letters, optional sep, 1-2 digits, optional sep,
# optional 1-2 letters, optional sep, 1-4 digits.
REG_RE = re.compile(
    r"\b[A-Z]{2}[ \-]?[0-9]{1,2}[ \-]?[A-Z]{0,2}[ \-]?[0-9]{1,4}\b",
    re.IGNORECASE)
NAME_RE = re.compile(r"\b(?:my\s+name\s+(?:is)?|this\s+is)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?",
                     re.IGNORECASE)

REDACTIONS: List[Tuple[re.Pattern[str], str]] = [
    (EMAIL_RE, "[EMAIL]"),
    (CARD_RE, "[CARD_NUMBER]"),
    (PHONE_RE, "[PHONE]"),
    (NAME_RE, "[NAME]"),
    (REG_RE, "[VEHICLE_REG]"),
]


def redact_pii(text: str) -> str:
    """Return `text` with best-effort PII replaced by placeholders."""
    if not text:
        return ""
    out = text
    for pattern, repl in REDACTIONS:
        out = pattern.sub(repl, out)
    return out


def contains_pii(text: str) -> bool:
    """True if any redaction pattern matches (used by tests)."""
    return any(p.search(text or "") for p, _ in REDACTIONS)
