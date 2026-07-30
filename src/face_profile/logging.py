"""Structured, privacy-conscious application logging."""

import json
import logging
import re
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from typing import TextIO
from uuid import UUID

_ALLOWED_CONTEXT = (
    "event_type",
    "camera_id",
    "track_id",
    "profile_id",
    "candidate_id",
    "correlation_id",
    "duration_ms",
    "face_count",
    "state",
    "error_code",
)

_SENSITIVE_MESSAGE_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|credential)"
        r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(
        r"(?i)\b(embedding|frame|image|crop)\s*[:=]\s*"
        r"(?:\[[^\]]*\]|\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
)


def _redact_message(message: str) -> str:
    for pattern in _SENSITIVE_MESSAGE_PATTERNS:
        message = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)
    return message


def _message_template(record: logging.LogRecord) -> str:
    if not isinstance(record.msg, str):
        return "<non-string-message>"
    return _redact_message(record.msg)


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else "<invalid-number>"
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    return "<unsupported>"


class JsonFormatter(logging.Formatter):
    """Serialize only approved log-record context fields as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": _message_template(record),
        }
        for field in _ALLOWED_CONTEXT:
            value = getattr(record, field, None)
            if value is not None:
                event[field] = _json_safe(value)
        return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


def configure_logging(*, level: str, stream: TextIO) -> None:
    """Configure the package logger with one deterministic JSON handler."""

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("face_profile")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
