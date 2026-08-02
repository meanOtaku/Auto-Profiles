"""RED-first tests proving the ONNX embedding adapter's preprocessing
(mean/scale) is genuinely configurable rather than hardcoded to one model
family's convention.

Auditing the current implementation against the real, independently
verified provenance of OpenCV Zoo's SFace model
(``face_recognition_sface_2021dec.onnx``, sha256
``0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79``, see
docs/MODELS.md) found that ``cv::FaceRecognizerSF::feature`` preprocesses
with ``blobFromImage(img, 1, Size(112,112), Scalar(0,0,0), swapRB=true)``
-- scale factor 1, zero mean -- which is numerically incompatible with
this adapter's previously hardcoded ArcFace-style constants (mean 127.5,
scale 1/128). Pinning SFace against the unmodified adapter would silently
feed the model badly-scaled input and produce meaningless embeddings, so
mean/scale must be configuration, not a fixed class constant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from face_profile.config import EmbeddingConfig
from face_profile.vision.alignment import AlignedFace
from face_profile.vision.embedding import OnnxEmbeddingGenerator, create_embedding_generator


class CapturingBackend:
    """Records the preprocessed blob handed to inference, changes nothing."""

    def __init__(self) -> None:
        self.blobs: list[NDArray[np.float32]] = []

    def infer(self, blob: NDArray[np.float32]) -> NDArray[Any]:
        self.blobs.append(blob)
        return np.arange(1, 9, dtype=np.float32)


def _constant_aligned_face(value: int) -> AlignedFace:
    image = np.full((4, 4, 3), value, dtype=np.uint8)
    return AlignedFace(image=image, output_width=4, output_height=4)


def test_generator_applies_configured_mean_and_scale_not_a_hardcoded_constant() -> None:
    backend = CapturingBackend()
    generator = OnnxEmbeddingGenerator(
        backend=backend,
        model_name="test-model",
        model_version="1",
        model_checksum="deadbeef",
        dimension=8,
        input_width=4,
        input_height=4,
        input_mean=0.0,
        input_scale=1.0,
    )

    generator.generate(_constant_aligned_face(200))

    blob = backend.blobs[0]
    # mean=0, scale=1 must leave raw pixel magnitude unchanged (up to the
    # channel swap, which is a no-op on a constant-valued image).
    assert blob.min() == pytest.approx(200.0)
    assert blob.max() == pytest.approx(200.0)


def test_generator_honors_a_different_configured_mean_and_scale() -> None:
    backend = CapturingBackend()
    generator = OnnxEmbeddingGenerator(
        backend=backend,
        model_name="test-model",
        model_version="1",
        model_checksum="deadbeef",
        dimension=8,
        input_width=4,
        input_height=4,
        input_mean=127.5,
        input_scale=1.0 / 128.0,
    )

    generator.generate(_constant_aligned_face(200))

    blob = backend.blobs[0]
    expected = (200.0 - 127.5) / 128.0
    assert blob.min() == pytest.approx(expected)
    assert blob.max() == pytest.approx(expected)


def test_create_embedding_generator_wires_configured_mean_and_scale(tmp_path: Path) -> None:
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(b"not-a-real-model-just-integrity-checked-bytes")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()

    captured: dict[str, CapturingBackend] = {}

    def fake_factory(model_bytes: bytes) -> CapturingBackend:
        backend = CapturingBackend()
        captured["backend"] = backend
        return backend

    config = EmbeddingConfig(
        enabled=True,
        backend="onnx",
        model_path=model_path,
        model_sha256=digest,
        model_name="sface",
        model_version="2021dec",
        dimension=8,
        input_width=32,
        input_height=32,
        input_mean=0.0,
        input_scale=1.0,
    )

    generator = create_embedding_generator(config, backend_factory=fake_factory)
    assert generator is not None

    generator.generate(_constant_aligned_face(50))

    blob = captured["backend"].blobs[0]
    assert blob.min() == pytest.approx(50.0)
    assert blob.max() == pytest.approx(50.0)
