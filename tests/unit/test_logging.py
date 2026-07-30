import io
import json
import logging
from uuid import UUID


def test_structured_logger_emits_allowlisted_context() -> None:
    from face_profile.logging import configure_logging

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    logger = logging.getLogger("face_profile.test")

    logger.info(
        "service state changed",
        extra={"event_type": "ServiceStarted", "correlation_id": "test-1", "ignored": "x"},
    )

    event = json.loads(stream.getvalue())
    assert event["level"] == "INFO"
    assert event["message"] == "service state changed"
    assert event["event_type"] == "ServiceStarted"
    assert event["correlation_id"] == "test-1"
    assert "ignored" not in event
    assert event["timestamp"].endswith("Z")


def test_structured_logger_drops_sensitive_unapproved_context() -> None:
    from face_profile.logging import configure_logging

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)

    logging.getLogger("face_profile.test").info(
        "candidate inspected",
        extra={
            "event_type": "CandidateInspected",
            "password": "do-not-log",
            "embedding": [0.1, 0.2],
            "frame": b"private-image",
            "metadata": {"token": "do-not-log"},
        },
    )

    serialized = stream.getvalue()
    event = json.loads(serialized)
    assert event["event_type"] == "CandidateInspected"
    assert "do-not-log" not in serialized
    assert "embedding" not in event
    assert "frame" not in event
    assert "metadata" not in event


def test_structured_logger_serializes_uuid_identifiers() -> None:
    from face_profile.logging import configure_logging

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    profile_id = UUID("12345678-1234-5678-1234-567812345678")

    logging.getLogger("face_profile.test").info(
        "profile selected",
        extra={"event_type": "ProfileSelected", "profile_id": profile_id},
    )

    event = json.loads(stream.getvalue())
    assert event["profile_id"] == str(profile_id)


def test_structured_logger_redacts_sensitive_message_values() -> None:
    from face_profile.logging import configure_logging

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)

    logging.getLogger("face_profile.test").info(
        "credential=super-secret embedding=[0.1, 0.2] token: abcdefghijklmnop"
    )

    serialized = stream.getvalue()
    event = json.loads(serialized)
    assert "super-secret" not in serialized
    assert "0.1" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert "[REDACTED]" in event["message"]


def test_structured_logger_never_interpolates_positional_arguments() -> None:
    from face_profile.logging import configure_logging

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)

    logging.getLogger("face_profile.test").info(
        "processed payload %s",
        b"private-face-payload",
    )

    serialized = stream.getvalue()
    event = json.loads(serialized)
    assert "private-face-payload" not in serialized
    assert event["message"] == "processed payload %s"
