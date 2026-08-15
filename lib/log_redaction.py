# -*- coding: utf-8 -*-
"""Redact credentials and session data before they reach any log sink."""

from __future__ import annotations

import re


_REDACTED = "<redacted>"
_SENSITIVE_KEYS = (
    "password|passwd|pwd|username|account|email|token|secret|"
    "authorization|cookie|cookies"
)
_QUOTED_VALUE_RE = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEYS})['\"]?\s*[:=]\s*)"
    rf"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_UNQUOTED_VALUE_RE = re.compile(
    rf"(?P<prefix>\b(?:{_SENSITIVE_KEYS})\b\s*[:=]\s*)"
    r"(?P<value>[^\s,;}&\]]+)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def redact_sensitive_data(message: object) -> str:
    text = str(message)
    text = _QUOTED_VALUE_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{_REDACTED}{match.group('quote')}",
        text,
    )
    text = _UNQUOTED_VALUE_RE.sub(
        lambda match: f"{match.group('prefix')}{_REDACTED}",
        text,
    )
    return _EMAIL_RE.sub(_REDACTED, text)
