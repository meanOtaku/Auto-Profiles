"""Face embedding generation and cosine-similarity comparison.

M5 turns an aligned M4 crop into a normalized embedding vector and provides
pairwise similarity. It does not decide identity: recognition state,
threshold/margin decisions over live tracks, and temporal confirmation
belong to M7. No production embedding model is bundled or selected as a
default in M5; see ADR 0002 and ADR 0010.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol, TypeAlias, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from face_profile.config import EmbeddingConfig
from face_profile.vision.alignment import AlignedFace

EmbeddingVector: TypeAlias = NDArray[np.float32]

_OPENCV_LOGGING_LOCK = Lock()


class EmbeddingError(RuntimeError):
    """Raised when an embedding cannot be generated, normalized, or compared."""


class EmbeddingModelError(RuntimeError):
    """Raised when an embedding model cannot be trusted or loaded safely."""


@dataclass(frozen=True, slots=True)
class FaceEmbedding:
    """A normalized embedding with the model provenance required to compare it."""

    vector: EmbeddingVector = field(repr=False)
    model_name: str
    model_version: str
    model_checksum: str
    dimension: int
    numeric_dtype: str


class EmbeddingGenerator(Protocol):
    """Generate a normalized embedding from one aligned face crop."""

    def generate(self, aligned: AlignedFace) -> FaceEmbedding: ...


def normalize(vector: NDArray[np.floating[Any]]) -> EmbeddingVector:
    """L2-normalize a vector, rejecting degenerate near-zero-norm input."""

    as_float32 = vector.astype(np.float32, copy=False)
    norm = float(np.linalg.norm(as_float32))
    if not np.isfinite(norm) or norm < 1e-12:
        raise EmbeddingError("embedding vector has no reliable direction")
    return cast(EmbeddingVector, (as_float32 / norm).astype(np.float32))


def cosine_similarity(a: FaceEmbedding, b: FaceEmbedding) -> float:
    """Compare two normalized embeddings produced by a compatible model.

    Comparing embeddings across incompatible model names, versions, or
    dimensions is rejected rather than silently computed, per
    CODING_STANDARDS.md's model-compatibility rule.
    """

    if a.model_name != b.model_name or a.model_version != b.model_version:
        raise EmbeddingError("cannot compare embeddings from incompatible models")
    if a.dimension != b.dimension or a.vector.shape != b.vector.shape:
        raise EmbeddingError("cannot compare embeddings of different dimension")
    similarity = float(np.dot(a.vector, b.vector))
    return max(-1.0, min(1.0, similarity))


class MockEmbeddingGenerator:
    """Deterministic content-derived embedding generator that uses no model.

    A stable hash of the aligned crop's pixel content seeds a normalized
    pseudo-random vector, so identical content always produces an identical
    vector and differing content produces a different one. This supports
    similarity-contract testing and hardware-free operation without a
    trained model or any real biometric data.
    """

    MODEL_NAME = "mock-content-hash"
    MODEL_VERSION = "1"
    DIMENSION = 128

    def generate(self, aligned: AlignedFace) -> FaceEmbedding:
        """Derive a deterministic normalized vector from crop content."""

        digest = hashlib.sha256(aligned.image.tobytes()).digest()
        seed = int.from_bytes(digest[:8], "big")
        rng = np.random.default_rng(seed)
        raw = rng.standard_normal(self.DIMENSION).astype(np.float32)
        return FaceEmbedding(
            vector=normalize(raw),
            model_name=self.MODEL_NAME,
            model_version=self.MODEL_VERSION,
            model_checksum="none",
            dimension=self.DIMENSION,
            numeric_dtype="float32",
        )


class OnnxEmbeddingBackend(Protocol):
    """Narrow protocol for OpenCV-DNN-compatible embedding backends."""

    def infer(self, blob: NDArray[np.float32]) -> NDArray[Any]: ...


class _OpenCvDnnEmbeddingBackend:
    """Adapts an OpenCV DNN network to the narrow backend protocol."""

    def __init__(self, net: Any) -> None:
        self._net = net

    def infer(self, blob: NDArray[np.float32]) -> NDArray[Any]:
        self._net.setInput(blob)
        return cast(NDArray[Any], self._net.forward())


class OnnxEmbeddingGenerator:
    """Generate embeddings from an integrity-verified ONNX model via OpenCV DNN."""

    # Standard ArcFace-family preprocessing: BGR input, mean-subtracted and
    # scaled to approximately [-1, 1]. This matches the documented input
    # contract for the configured model and is a fixed calibration, not a
    # configurable recognition threshold.
    _MEAN = (127.5, 127.5, 127.5)
    _SCALE = 1.0 / 128.0

    def __init__(
        self,
        *,
        backend: OnnxEmbeddingBackend,
        model_name: str,
        model_version: str,
        model_checksum: str,
        dimension: int,
        input_width: int,
        input_height: int,
    ) -> None:
        self._backend = backend
        self._model_name = model_name
        self._model_version = model_version
        self._model_checksum = model_checksum
        self._dimension = dimension
        self._input_size = (input_width, input_height)

    def generate(self, aligned: AlignedFace) -> FaceEmbedding:
        """Preprocess, run inference, validate, and normalize the output."""

        try:
            blob = cv2.dnn.blobFromImage(
                aligned.image,
                scalefactor=self._SCALE,
                size=self._input_size,
                mean=self._MEAN,
                swapRB=True,
                crop=False,
            )
        except cv2.error as error:
            raise EmbeddingError("embedding preprocessing failed") from error
        try:
            raw_output = self._backend.infer(blob.astype(np.float32))
        except Exception:
            raise EmbeddingError("embedding backend inference failed") from None
        try:
            flattened = np.asarray(raw_output).reshape(-1)
        except Exception:
            raise EmbeddingError("embedding backend returned invalid output") from None
        if flattened.shape[0] != self._dimension:
            raise EmbeddingError("embedding backend returned an unexpected dimension")
        if not np.isfinite(flattened).all():
            raise EmbeddingError("embedding backend returned non-finite values")
        return FaceEmbedding(
            vector=normalize(flattened.astype(np.float32)),
            model_name=self._model_name,
            model_version=self._model_version,
            model_checksum=self._model_checksum,
            dimension=self._dimension,
            numeric_dtype="float32",
        )


def create_embedding_generator(
    config: EmbeddingConfig,
    *,
    backend_factory: Any = None,
) -> EmbeddingGenerator | None:
    """Build the configured embedding generator after model-integrity validation."""

    if not config.enabled:
        return None
    if config.backend == "mock":
        return MockEmbeddingGenerator()

    model_path = config.model_path
    expected_sha256 = config.model_sha256
    if model_path is None or expected_sha256 is None:
        raise EmbeddingModelError("embedding model configuration is incomplete")
    try:
        model_bytes = model_path.read_bytes()
    except OSError:
        raise EmbeddingModelError("embedding model unavailable") from None
    if not hmac.compare_digest(hashlib.sha256(model_bytes).hexdigest(), expected_sha256):
        raise EmbeddingModelError("embedding model integrity check failed")
    factory = backend_factory if backend_factory is not None else _create_opencv_dnn_backend
    try:
        backend = factory(model_bytes)
    except Exception:
        raise EmbeddingModelError("embedding model loading failed") from None
    return OnnxEmbeddingGenerator(
        backend=backend,
        model_name=config.model_name,
        model_version=config.model_version,
        model_checksum=expected_sha256,
        dimension=config.dimension,
        input_width=config.input_width,
        input_height=config.input_height,
    )


def _create_opencv_dnn_backend(model_bytes: bytes) -> OnnxEmbeddingBackend:
    opencv_logging = cast(Any, cv2.utils.logging)
    with _OPENCV_LOGGING_LOCK:
        previous_log_level = opencv_logging.getLogLevel()
        opencv_logging.setLogLevel(opencv_logging.LOG_LEVEL_SILENT)
        try:
            net = cv2.dnn.readNetFromONNX(np.frombuffer(model_bytes, dtype=np.uint8))
        finally:
            opencv_logging.setLogLevel(previous_log_level)
    return _OpenCvDnnEmbeddingBackend(net)
