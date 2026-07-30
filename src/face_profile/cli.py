"""Command-line entry point for the headless service foundation."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from face_profile.camera.factory import create_frame_source
from face_profile.config import ConfigurationError, load_config
from face_profile.logging import configure_logging
from face_profile.service import Service
from face_profile.settings import DeviceSettings, MockSettingsAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="face-profile")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("command", choices=("check",))
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run a one-shot hardware-free service lifecycle check."""

    output = stdout if stdout is not None else sys.stdout
    error_output = stderr if stderr is not None else sys.stderr
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigurationError:
        configure_logging(level="ERROR", stream=error_output)
        logging.getLogger("face_profile.cli").error(
            "startup configuration rejected",
            extra={"event_type": "StartupFailed", "error_code": "invalid_configuration"},
        )
        return 2
    configure_logging(level=config.logging.level, stream=output)
    logger = logging.getLogger("face_profile.cli")
    settings = MockSettingsAdapter(
        DeviceSettings(
            volume=config.settings.volume,
            brightness=config.settings.brightness,
        )
    )
    service = Service(
        config=config,
        settings=settings,
        frame_source=create_frame_source(config.camera),
    )
    try:
        service.start()
    except Exception:
        cleanup_failed = False
        try:
            service.stop()
        except Exception:
            cleanup_failed = True
        configure_logging(level="ERROR", stream=error_output)
        logging.getLogger("face_profile.cli").error(
            "service startup failed",
            extra={
                "event_type": "StartupFailed",
                "error_code": (
                    "service_start_cleanup_failed" if cleanup_failed else "service_start_failed"
                ),
            },
        )
        return 3
    logger.info("service started", extra={"event_type": "ServiceStarted", "state": service.state})
    try:
        service.stop()
    except Exception:
        configure_logging(level="ERROR", stream=error_output)
        logging.getLogger("face_profile.cli").error(
            "service shutdown failed",
            extra={"event_type": "ShutdownFailed", "error_code": "service_stop_failed"},
        )
        return 4
    logger.info("service stopped", extra={"event_type": "ServiceStopped", "state": service.state})
    return 0
