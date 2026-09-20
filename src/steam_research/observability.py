"""Structured local diagnostics with conservative redaction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|secret|password|credential)"
)
_URL_CREDENTIALS = re.compile(r"(https?://)([^/@\s]+):([^/@\s]+)@")
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|credential)(\s*[=:]\s*)[^\s,;]+"
)
_BARE_SECRET = re.compile(r"(?i)\b(secret|password|credential)\b")


@dataclass(frozen=True)
class Diagnostic:
    run_id: str
    stage: str
    app_id: int | None = None
    unit_id: str | None = None
    stream_batch_id: str | None = None
    attempt: int | None = None
    duration_ms: int | None = None
    result_count: int | None = None
    error_classification: str | None = None
    message: str | None = None


def redact_text(value: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Redact credentials, URL userinfo, and configured secret values."""
    text = value.replace("\x00", "�")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _URL_CREDENTIALS.sub(r"\1[redacted]@", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _ASSIGNMENT.sub(r"\1\2[redacted]", text)
    return _BARE_SECRET.sub("[redacted]", text)


def redact_value(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact diagnostic values without retaining untrusted payloads."""
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SECRET_KEY.search(str(key))
            else redact_value(child, secrets=secrets)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(child, secrets=secrets) for child in value]
    if isinstance(value, str):
        return redact_text(value, secrets=secrets)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value), secrets=secrets)


def classify_error(error: BaseException) -> str:
    """Return a stable, non-content-bearing error category."""
    name = type(error).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connection" in name or isinstance(error, OSError):
        return "connection"
    if "validation" in name or isinstance(error, ValueError):
        return "validation"
    if "permission" in name:
        return "permission"
    return "unexpected"


def diagnostic_json(diagnostic: Diagnostic) -> str:
    """Serialize only the bounded diagnostic contract."""
    return json.dumps(redact_value(diagnostic.__dict__), sort_keys=True)


def redact_url(value: str) -> str:
    """Remove URL credentials while preserving the endpoint shape."""
    parsed = urlsplit(value)
    if parsed.username is None and parsed.password is None:
        return value
    return urlunsplit(
        (parsed.scheme, "[redacted]", parsed.path, parsed.query, parsed.fragment)
    )
