# ADR 0010: Embedding Generation and Similarity Boundary

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M5

## Context

M5 must generate normalized embeddings from M4 aligned crops, compare them with cosine similarity, and deliver a threshold-evaluation capability that reports false accepts/rejects, plus a configurable initial threshold and margin. ADR 0002 blocks any production embedding model until provenance, license, checksum, input/output contract, and evaluation results are recorded. No approved biometric evaluation dataset exists in this repository, and none may be committed per CONTRIBUTING.md. M5 must not implement the M7 recognition-decision state machine (KNOWN/AMBIGUOUS/UNKNOWN, temporal confirmation) — only the primitives that decision logic will consume.

## Decision

Add `embedding.py` defining an immutable `FaceEmbedding` (vector, model name/version/checksum, dimension, dtype), an `EmbeddingGenerator` protocol, `normalize()`, and `cosine_similarity()`. Similarity comparison rejects mismatched model name, version, or dimension rather than silently comparing incompatible vectors, per CODING_STANDARDS.md's model-compatibility rule.

Two generators are provided:

- `MockEmbeddingGenerator` derives a deterministic, normalized pseudo-random vector from a SHA-256 hash of the aligned crop's pixel content. Identical content always yields an identical vector; different content yields a different one. This exercises the similarity contract without any trained model or real biometric data, matching the M1–M4 mock-first pattern.
- `OnnxEmbeddingGenerator` runs an integrity-verified ONNX model through OpenCV's DNN module (`cv2.dnn.readNetFromONNX`), avoiding a new `onnxruntime` dependency since `opencv-python-headless` is already a project dependency and can execute ONNX graphs on CPU. It follows the exact SHA-256-verified, exact-byte-buffer loading pattern `vision/factory.py` established for the M2 YuNet detector. Preprocessing (mean-subtraction, 1/128 scale, BGR-to-RGB swap) is the documented ArcFace-family input convention, not a configurable threshold.

`create_embedding_generator()` returns `None` unless `embedding.enabled` is true, matching the M1–M4 disabled-by-default factory pattern. No embedding model artifact is downloaded, bundled, or selected as a production default in this milestone; procuring and rights-reviewing a specific ArcFace-compatible artifact (per HERMES's licensing-verification requirement) remains an explicit follow-up before production use, consistent with ADR 0002.

Add `threshold_evaluation.py` with `PairScore`, `ThresholdMetrics`, `ScoreStatistics`, and `EvaluationReport`. `evaluate_thresholds()` computes false-accept/false-reject rates per swept threshold, per-class score statistics with a normal-approximation 95% confidence interval, an equal-error-rate estimate (interpolated when the sweep brackets a sign change, otherwise the closest single point), and cohort labels, operating only on caller-supplied `PairScore` values. It fabricates no evaluation evidence: without a caller-supplied, approved, privacy-safe labeled dataset, no accuracy claim is produced. `EmbeddingConfig` also carries `similarity_threshold` and `similarity_margin` as the "configurable initial threshold and margin" ROADMAP.md requires M5 to select; both are documented as unevaluated conservative starting points for M7 to consume, not production-validated values.

The CLI gains `compare` (runs the full detect → quality → align → embed pipeline on two images and prints cosine similarity) and `evaluate-threshold` (reads a caller-supplied JSON pair file and prints the full evaluation report), completing the two HERMES-required CLI verbs this milestone owns.

## Consequences

- M4 alignment output is consumed unchanged; M5 introduces no new pixel retention beyond what M4 already bounds.
- Embeddings are never logged; CLI commands print only similarity scalars and evaluation statistics, never vectors.
- Real accuracy evidence (genuine/impostor distributions on approved data, FAR/FRR at a chosen operating point, rank-1 accuracy) remains unproduced until an approved dataset and a rights-reviewed model artifact exist; this is a known gap carried forward, not a completed exit criterion.
- M6 owns durable embedding storage; M7 owns the recognition-decision state machine built on `cosine_similarity()` and the configured threshold/margin.
