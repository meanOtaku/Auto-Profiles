"""Command-line entry point for the headless service foundation."""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import UUID

from face_profile.camera import ImageFrameSource, save_frame
from face_profile.camera.factory import create_frame_source
from face_profile.config import AppConfig, ConfigurationError, load_config
from face_profile.database.candidate_repository import (
    CandidateNotFoundError,
    CandidateRepository,
    CandidateStateError,
)
from face_profile.database.factory import create_profile_database
from face_profile.database.keys import LocalFileKeyProvider
from face_profile.database.models import Candidate, CandidateStatus, Profile, ProfileStatus
from face_profile.database.repository import (
    ConcurrencyConflictError,
    ProfileNotFoundError,
    ProfileRepositoryError,
)
from face_profile.enrollment.factory import create_candidate_promoter
from face_profile.enrollment.promotion import (
    CandidateNotReadyError,
    DuplicateProfileError,
    PromotionError,
)
from face_profile.logging import configure_logging
from face_profile.recognition.factory import create_recognizer
from face_profile.service import Service
from face_profile.settings import DeviceSettings, MockSettingsAdapter
from face_profile.vision.alignment import AlignmentError, FaceAligner, create_aligner
from face_profile.vision.detection import DetectionError, FaceDetector, render_detection_debug
from face_profile.vision.embedding import (
    EmbeddingError,
    EmbeddingGenerator,
    EmbeddingModelError,
    FaceEmbedding,
    cosine_similarity,
    create_embedding_generator,
)
from face_profile.vision.factory import DetectionModelError, create_face_detector
from face_profile.vision.quality import QualityEvaluator, create_quality_evaluator
from face_profile.vision.threshold_evaluation import (
    PairScore,
    ThresholdEvaluationError,
    evaluate_thresholds,
)

_DEFAULT_THRESHOLD_SWEEP = tuple(round(-1.0 + 0.05 * step, 2) for step in range(41))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="face-profile")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    detect = commands.add_parser("detect")
    detect.add_argument("--debug-output", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("--image-a", type=Path, required=True)
    compare.add_argument("--image-b", type=Path, required=True)
    evaluate_threshold = commands.add_parser("evaluate-threshold")
    evaluate_threshold.add_argument("--pairs", type=Path, required=True)
    evaluate_threshold.add_argument("--thresholds", type=str, default=None)
    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_create = profile_commands.add_parser("create")
    profile_create.add_argument("--display-name", required=True)
    profile_create.add_argument("--owner", action="store_true")
    profile_create.add_argument("--priority", type=int, default=0)
    profile_list = profile_commands.add_parser("list")
    profile_list.add_argument("--status", choices=[status.value for status in ProfileStatus])
    profile_show = profile_commands.add_parser("show")
    profile_show.add_argument("--id", required=True)
    profile_delete = profile_commands.add_parser("delete")
    profile_delete.add_argument("--id", required=True)
    profile_delete.add_argument("--expected-version", type=int, required=True)
    profile_merge = profile_commands.add_parser("merge")
    profile_merge.add_argument("--source-id", required=True)
    profile_merge.add_argument("--target-id", required=True)
    profile_merge.add_argument("--source-expected-version", type=int, required=True)
    profile_merge.add_argument("--target-expected-version", type=int, required=True)
    recognize = commands.add_parser("recognize")
    recognize.add_argument("--image", type=Path, required=True)
    recognize.add_argument("--track-id", type=int, default=0)
    candidate = commands.add_parser("candidate")
    candidate_commands = candidate.add_subparsers(dest="candidate_command", required=True)
    candidate_list = candidate_commands.add_parser("list")
    candidate_list.add_argument("--status", choices=[status.value for status in CandidateStatus])
    candidate_show = candidate_commands.add_parser("show")
    candidate_show.add_argument("--id", required=True)
    candidate_approve = candidate_commands.add_parser("approve")
    candidate_approve.add_argument("--id", required=True)
    candidate_approve.add_argument("--reviewed-by", required=True)
    candidate_reject = candidate_commands.add_parser("reject")
    candidate_reject.add_argument("--id", required=True)
    candidate_reject.add_argument("--reason", required=True)
    candidate_reject.add_argument("--reviewed-by")
    return parser


class _PipelineInputError(ValueError):
    """Raised when one image cannot produce an embedding for comparison."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _embed_one_image(
    path: Path,
    *,
    detector: FaceDetector,
    quality_evaluator: QualityEvaluator,
    aligner: FaceAligner,
    embedder: EmbeddingGenerator,
) -> FaceEmbedding:
    frame_source = ImageFrameSource(path)
    frame_source.open()
    try:
        frame = frame_source.read()
    finally:
        frame_source.close()
    detections = detector.detect(frame)
    if len(detections) == 0:
        raise _PipelineInputError("no_face_detected")
    best = detections[0]
    quality = quality_evaluator.evaluate(frame, best)
    if not quality.accepted:
        raise _PipelineInputError("quality_rejected")
    aligned = aligner.align(frame, best)
    return embedder.generate(aligned)


def _profile_to_json(profile: Profile) -> dict[str, object]:
    return {
        "id": str(profile.id),
        "display_name": profile.display_name,
        "status": profile.status.value,
        "priority": profile.priority,
        "is_owner": profile.is_owner,
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
        "last_seen_at": profile.last_seen_at.isoformat() if profile.last_seen_at else None,
        "optimistic_version": profile.optimistic_version,
    }


def _candidate_to_json(candidate: Candidate) -> dict[str, object]:
    return {
        "id": str(candidate.id),
        "temporary_name": candidate.temporary_name,
        "status": candidate.status.value,
        "first_seen_at": candidate.first_seen_at.isoformat(),
        "last_seen_at": candidate.last_seen_at.isoformat(),
        "sample_count": candidate.sample_count,
        "aggregate_quality": candidate.aggregate_quality,
        "review_status": candidate.review_status,
        "reviewed_by": candidate.reviewed_by,
        "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
        "promoted_profile_id": (
            str(candidate.promoted_profile_id) if candidate.promoted_profile_id else None
        ),
    }


def _build_comparison_pipeline(
    config: AppConfig,
) -> tuple[FaceDetector, QualityEvaluator, FaceAligner, EmbeddingGenerator] | None:
    detector = create_face_detector(config.detection)
    quality_evaluator = create_quality_evaluator(config.quality)
    aligner = create_aligner(config.quality)
    embedder = create_embedding_generator(config.embedding)
    if detector is None or quality_evaluator is None or aligner is None or embedder is None:
        return None
    return detector, quality_evaluator, aligner, embedder


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
    if args.command == "detect":
        frame_source = create_frame_source(config.camera)
        try:
            detector = create_face_detector(config.detection)
        except DetectionModelError:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "detector startup failed",
                extra={"event_type": "DetectionFailed", "error_code": "detector_unavailable"},
            )
            return 3
        if frame_source is None or detector is None:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "detection is disabled",
                extra={"event_type": "DetectionFailed", "error_code": "detection_disabled"},
            )
            return 3
        try:
            frame_source.open()
            frame = frame_source.read()
            if frame is None:
                raise DetectionError("camera source produced no frame")
            detections = detector.detect(frame)
            if args.debug_output is not None:
                save_frame(render_detection_debug(frame, detections), args.debug_output)
        except Exception:
            cleanup_failed = False
            try:
                frame_source.close()
            except Exception:
                cleanup_failed = True
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "face detection failed",
                extra={
                    "event_type": "DetectionFailed",
                    "error_code": (
                        "detection_cleanup_failed" if cleanup_failed else "detection_failed"
                    ),
                },
            )
            return 4 if cleanup_failed else 3
        try:
            frame_source.close()
        except Exception:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "camera shutdown failed",
                extra={"event_type": "DetectionFailed", "error_code": "camera_close_failed"},
            )
            return 4
        logger.info(
            "face detection completed",
            extra={"event_type": "DetectionCompleted", "face_count": len(detections)},
        )
        return 0

    if args.command == "compare":
        try:
            pipeline = _build_comparison_pipeline(config)
        except (DetectionModelError, EmbeddingModelError):
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "comparison model startup failed",
                extra={"event_type": "ComparisonFailed", "error_code": "model_unavailable"},
            )
            return 3
        if pipeline is None:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "comparison is disabled",
                extra={"event_type": "ComparisonFailed", "error_code": "comparison_disabled"},
            )
            return 3
        detector, quality_evaluator, aligner, embedder = pipeline
        try:
            embedding_a = _embed_one_image(
                args.image_a,
                detector=detector,
                quality_evaluator=quality_evaluator,
                aligner=aligner,
                embedder=embedder,
            )
            embedding_b = _embed_one_image(
                args.image_b,
                detector=detector,
                quality_evaluator=quality_evaluator,
                aligner=aligner,
                embedder=embedder,
            )
            similarity = cosine_similarity(embedding_a, embedding_b)
        except _PipelineInputError as error:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "comparison input rejected",
                extra={"event_type": "ComparisonFailed", "error_code": error.error_code},
            )
            return 3
        except (DetectionError, AlignmentError, EmbeddingError):
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "comparison failed",
                extra={"event_type": "ComparisonFailed", "error_code": "comparison_failed"},
            )
            return 3
        logger.info(
            "face comparison completed",
            extra={"event_type": "ComparisonCompleted", "similarity": round(similarity, 6)},
        )
        output.write(json.dumps({"similarity": similarity}) + "\n")
        return 0

    if args.command == "evaluate-threshold":
        try:
            raw_pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
            pairs = tuple(
                PairScore(
                    similarity=float(entry["similarity"]),
                    is_genuine=bool(entry["is_genuine"]),
                    cohort=entry.get("cohort"),
                )
                for entry in raw_pairs
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "threshold evaluation input rejected",
                extra={"event_type": "EvaluationFailed", "error_code": "invalid_pairs_file"},
            )
            return 2
        if args.thresholds is not None:
            thresholds = tuple(float(value) for value in args.thresholds.split(","))
        else:
            thresholds = _DEFAULT_THRESHOLD_SWEEP
        try:
            report = evaluate_thresholds(pairs, thresholds)
        except ThresholdEvaluationError:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "threshold evaluation rejected",
                extra={"event_type": "EvaluationFailed", "error_code": "invalid_dataset"},
            )
            return 2
        logger.info(
            "threshold evaluation completed",
            extra={"event_type": "EvaluationCompleted", "pair_count": len(pairs)},
        )
        output.write(json.dumps(asdict(report), indent=2) + "\n")
        return 0

    if args.command == "profile":
        database = create_profile_database(config.database)
        if database is None:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "profile database is disabled",
                extra={"event_type": "ProfileCommandFailed", "error_code": "database_disabled"},
            )
            return 3
        try:
            if args.profile_command == "create":
                profile = database.profiles.create(
                    display_name=args.display_name,
                    is_owner=args.owner,
                    priority=args.priority,
                )
                database.events.record(event_type="ProfileCreated", profile_id=profile.id)
                database.commit()
                logger.info(
                    "profile created",
                    extra={"event_type": "ProfileCreated", "profile_id": profile.id},
                )
                output.write(json.dumps(_profile_to_json(profile)) + "\n")
            elif args.profile_command == "list":
                status = ProfileStatus(args.status) if args.status else None
                profiles = database.profiles.list(status=status)
                logger.info("profiles listed", extra={"event_type": "ProfileListed"})
                output.write(json.dumps([_profile_to_json(p) for p in profiles]) + "\n")
            elif args.profile_command == "show":
                profile = database.profiles.get(UUID(args.id))
                output.write(json.dumps(_profile_to_json(profile)) + "\n")
            elif args.profile_command == "delete":
                profile = database.profiles.soft_delete(
                    UUID(args.id),
                    expected_version=args.expected_version,
                    retention_days=config.database.retention_days,
                )
                database.events.record(event_type="ProfileDeleted", profile_id=profile.id)
                database.commit()
                logger.info(
                    "profile deleted",
                    extra={"event_type": "ProfileDeleted", "profile_id": profile.id},
                )
                output.write(json.dumps(_profile_to_json(profile)) + "\n")
            else:
                profile = database.profiles.merge(
                    source_id=UUID(args.source_id),
                    target_id=UUID(args.target_id),
                    expected_source_version=args.source_expected_version,
                    expected_target_version=args.target_expected_version,
                )
                database.events.record(event_type="ProfileMerged", profile_id=profile.id)
                database.commit()
                logger.info(
                    "profiles merged",
                    extra={"event_type": "ProfileMerged", "profile_id": profile.id},
                )
                output.write(json.dumps(_profile_to_json(profile)) + "\n")
        except ValueError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "profile identifier invalid",
                extra={"event_type": "ProfileCommandFailed", "error_code": "invalid_identifier"},
            )
            database.close()
            return 2
        except ProfileNotFoundError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "profile not found",
                extra={"event_type": "ProfileCommandFailed", "error_code": "profile_not_found"},
            )
            database.close()
            return 3
        except ConcurrencyConflictError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "profile concurrency conflict",
                extra={"event_type": "ProfileCommandFailed", "error_code": "concurrency_conflict"},
            )
            database.close()
            return 3
        except ProfileRepositoryError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "profile command failed",
                extra={
                    "event_type": "ProfileCommandFailed",
                    "error_code": "profile_command_failed",
                },
            )
            database.close()
            return 3
        database.close()
        return 0

    if args.command == "candidate":
        database = create_profile_database(config.database)
        if database is None or not config.enrollment.enabled:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "candidate review requires the profile database and enrollment",
                extra={"event_type": "CandidateCommandFailed", "error_code": "enrollment_disabled"},
            )
            if database is not None:
                database.close()
            return 3
        key_provider = LocalFileKeyProvider(config.database.key_path)
        candidates = CandidateRepository(database.connection, key_provider=key_provider)
        try:
            if args.candidate_command == "list":
                candidate_status = CandidateStatus(args.status) if args.status else None
                found = candidates.list(status=candidate_status)
                logger.info("candidates listed", extra={"event_type": "CandidateListed"})
                output.write(json.dumps([_candidate_to_json(c) for c in found]) + "\n")
            elif args.candidate_command == "show":
                found_candidate = candidates.get(UUID(args.id))
                output.write(json.dumps(_candidate_to_json(found_candidate)) + "\n")
            elif args.candidate_command == "approve":
                promoter = create_candidate_promoter(config, database)
                if promoter is None:
                    raise CandidateStateError("enrollment is disabled")
                profile = promoter.promote(UUID(args.id), reviewed_by=args.reviewed_by)
                logger.info(
                    "candidate approved and promoted",
                    extra={
                        "event_type": "ProfileCreated",
                        "profile_id": profile.id,
                    },
                )
                output.write(json.dumps(_profile_to_json(profile)) + "\n")
            else:
                rejected_candidate = candidates.reject(
                    UUID(args.id), reason=args.reason, reviewed_by=args.reviewed_by
                )
                database.commit()
                logger.info(
                    "candidate rejected",
                    extra={"event_type": "CandidateUpdated", "state": "rejected"},
                )
                output.write(json.dumps(_candidate_to_json(rejected_candidate)) + "\n")
        except ValueError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "candidate identifier invalid",
                extra={
                    "event_type": "CandidateCommandFailed",
                    "error_code": "invalid_identifier",
                },
            )
            database.close()
            return 2
        except CandidateNotFoundError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "candidate not found",
                extra={
                    "event_type": "CandidateCommandFailed",
                    "error_code": "candidate_not_found",
                },
            )
            database.close()
            return 3
        except CandidateNotReadyError:
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "candidate is not ready for review",
                extra={"event_type": "CandidateCommandFailed", "error_code": "not_ready"},
            )
            database.close()
            return 3
        except DuplicateProfileError:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "candidate matches an existing profile and was rejected",
                extra={"event_type": "CandidateCommandFailed", "error_code": "duplicate_profile"},
            )
            database.close()
            return 3
        except (CandidateStateError, PromotionError):
            database.rollback()
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "candidate command failed",
                extra={
                    "event_type": "CandidateCommandFailed",
                    "error_code": "candidate_command_failed",
                },
            )
            database.close()
            return 3
        database.close()
        return 0

    if args.command == "recognize":
        database = create_profile_database(config.database)
        if database is None:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "recognition requires the profile database",
                extra={"event_type": "RecognitionFailed", "error_code": "database_disabled"},
            )
            return 3
        try:
            pipeline = _build_comparison_pipeline(config)
            recognizer = create_recognizer(config, database.profiles)
        except (DetectionModelError, EmbeddingModelError):
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "recognition model startup failed",
                extra={"event_type": "RecognitionFailed", "error_code": "model_unavailable"},
            )
            database.close()
            return 3
        if pipeline is None or recognizer is None:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "recognition is disabled",
                extra={"event_type": "RecognitionFailed", "error_code": "recognition_disabled"},
            )
            database.close()
            return 3
        detector, quality_evaluator, aligner, embedder = pipeline
        try:
            embedding = _embed_one_image(
                args.image,
                detector=detector,
                quality_evaluator=quality_evaluator,
                aligner=aligner,
                embedder=embedder,
            )
            decision = recognizer.recognize(args.track_id, embedding, observed_at=datetime.now(UTC))
        except _PipelineInputError as error:
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "recognition input rejected",
                extra={"event_type": "RecognitionFailed", "error_code": error.error_code},
            )
            database.close()
            return 3
        except (DetectionError, AlignmentError, EmbeddingError):
            configure_logging(level="ERROR", stream=error_output)
            logging.getLogger("face_profile.cli").error(
                "recognition failed",
                extra={"event_type": "RecognitionFailed", "error_code": "recognition_failed"},
            )
            database.close()
            return 3
        logger.info(
            "recognition completed",
            extra={
                "event_type": "ProfileRecognized",
                "state": decision.state.value,
                "profile_id": decision.profile_id,
            },
        )
        output.write(
            json.dumps(
                {
                    "state": decision.state.value,
                    "profile_id": str(decision.profile_id) if decision.profile_id else None,
                    "similarity": decision.similarity,
                    "second_best_similarity": decision.second_best_similarity,
                    "reason": decision.reason,
                }
            )
            + "\n"
        )
        database.close()
        return 0

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
