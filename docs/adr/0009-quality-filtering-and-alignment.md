# ADR 0009: Quality Filtering and Alignment Boundary

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M4

## Context

M4 must reject small, blurry, dark, overexposed, strongly rotated, and heavily occluded samples with a recorded reason for every rejection, align accepted crops to a fixed template for the M5 embedding model, and retain the best accepted crop per track. No trained occlusion, spoof, or liveness model is part of the recommended stack at this milestone; M14 owns trained anti-spoof evaluation. M4 must not perform embedding generation, matching, persistence, or enrollment.

## Decision

Add three independent, profile-independent modules under `vision/`:

- `quality.py` defines an immutable `QualityResult` (`accepted`, `score`, `reasons`) and a `QualityEvaluator` protocol. `HeuristicQualityEvaluator` runs five checks against a `QualityConfig`: face size, Laplacian-variance sharpness, mean-brightness/highlight-clipping exposure, eye-line roll and nose-symmetry yaw pose, and a landmark-geometry occlusion proxy (eye/nose/mouth vertical ordering, eye-distance plausibility, and a landmark-to-box margin check). Every failing check appends its own reason string; `accepted` is true only when no check fails. The occlusion proxy is explicitly documented as a geometric heuristic, not a trained occlusion classifier — a limitation carried forward to M14.
- `alignment.py` defines `AlignedFace` and a `FaceAligner` protocol. `SimilarityFaceAligner` estimates a similarity transform from the detector's five landmarks to the published InsightFace/ArcFace 112x112 reference template and warps the frame to a configurable output size. The reference template is a fixed model-input calibration, not a recognition threshold, so it is not exposed through configuration.
- `best_sample.py` defines `BestFaceSample` and `BestSampleTracker`, which retains at most one aligned crop per track — the highest-scoring **accepted** sample seen so far — bounded by a configured maximum track count, entirely in process memory. Rejected samples are refused by `consider()` so poor samples can never enter best-crop retention. Callers must call `discard()` when a track ends; the tracker performs no persistence, expiry timer, or disk I/O itself.

`create_quality_evaluator()` and `create_aligner()` both return `None` unless `quality.enabled` is explicitly true, matching the M1–M3 disabled-by-default factory pattern. All thresholds are configurable per CODING_STANDARDS.md and are documented as conservative starting points requiring local evaluation, not production-ready values.

## Consequences

- M2 detections and M3 tracks remain unchanged; quality and alignment consume them without mutation.
- `BestSampleTracker` is the first M-series component that retains pixel data beyond one frame's processing. Retention is bounded (one crop per track), in-memory only, and never automatically written to disk — the M1 owner-only frame writer remains the only persistence path, and it is not invoked automatically.
- The occlusion proxy will under- and over-reject relative to a trained model; this is a known, documented limitation until M14 delivers passive anti-spoof and occlusion evaluation.
- M5 owns embedding generation from `AlignedFace.image`; M4 does not generate or compare embeddings.
- M6/M8 own any durable storage of a retained crop; M4's retention is discarded when the process stops or the track ends.
