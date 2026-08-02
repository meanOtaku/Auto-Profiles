# Model Provenance

Exact provenance, license, and independently-confirmed SHA-256 for every pretrained
model this repository's tracked configs pin. No model file is bundled in this
repository or in this git history; `scripts/provision-models.sh` downloads and
verifies both files listed below into a local, gitignored `models/` directory.

Both entries were re-derived directly from the live upstream repository while
researching M16 (`docs/reports/M16.md`), not copied from memory or assumed correct
from an earlier note — see "How this was verified" under each entry.

## Face detector: YuNet (`face_detection_yunet_2023mar.onnx`)

- Upstream: [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo),
  `models/face_detection_yunet_2023mar/face_detection_yunet_2023mar.onnx`
- License: Apache 2.0 (see the `LICENSE` file in that directory)
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- Size: 232,589 bytes
- Contract: `cv2.FaceDetectorYN`-compatible ONNX graph; input is a BGR image at a
  configurable size, output includes bounding boxes, confidence, and 5-point
  landmarks. This repository's `vision/detection.py::YuNetFaceDetector` wraps
  `cv2.FaceDetectorYN.create()` directly against the verified bytes.
- Already used by `config/jetson-webcam.yaml` and `config/jetson-full.yaml`.

## Face embedding: SFace (`face_recognition_sface_2021dec.onnx`)

- Upstream: [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo),
  `models/face_recognition_sface/face_recognition_sface_2021dec.onnx`
- License: Apache 2.0 (see the `LICENSE` file in that directory); ONNX conversion of
  the original [zhongyy/SFace](https://github.com/zhongyy/SFace) research code
  ("SFace: Sigmoid-Constrained Hypersphere Loss for Robust Face Recognition",
  <https://arxiv.org/abs/2205.12010>). Review these upstream terms yourself before
  any commercial deployment, per HERMES.md's "Verify the licensing terms of all
  pretrained face-recognition models" instruction.
- SHA-256: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`
- Size: 38,696,353 bytes
- Contract (independently confirmed, not assumed):
  - Input: a 112x112 BGR aligned face crop, NCHW float32 blob.
  - **Preprocessing: `scalefactor=1`, `mean=(0,0,0)`, `swapRB=true`, `crop=false`**
    -- i.e. raw, unscaled 0-255 pixel values with a BGR->RGB channel swap and no
    mean subtraction. Confirmed directly against OpenCV's own C++ implementation
    of `cv::FaceRecognizerSF::feature()`
    (`modules/objdetect/src/face_recognize.cpp` in
    [opencv/opencv](https://github.com/opencv/opencv), `4.x` branch):
    `dnn::blobFromImage(_aligned_img, 1, Size(112, 112), Scalar(0, 0, 0), true, false)`.
  - Output: a 128-dimension float32 feature vector (confirmed by loading the real
    model file with `cv2.dnn.readNetFromONNX` and running a forward pass with a
    zero blob: output shape `(1, 128)`).
  - OpenCV Zoo's own published cosine-similarity match threshold is `0.363`
    (`models/face_recognition_sface/sface.py`'s `_threshold_cosine`) -- this is
    the upstream project's own value, not a locally evaluated one; no local
    same-person/different-person dataset evaluation has been run against this
    model in this repository (M5's exit criteria remain open, see
    `docs/reports/M5.md`).

### Why this needed an adapter code change, not just configuration

`vision/embedding.py::OnnxEmbeddingGenerator` previously hardcoded
`mean=(127.5,127.5,127.5)` / `scalefactor=1/128` as class constants -- the
InsightFace/ArcFace-family convention, not SFace's. Plugging SFace's weights into
that unmodified adapter would have fed the model input scaled to roughly `[-1, 1]`
when it expects raw `[0, 255]` values: a numerically different distribution than
the model was trained on, silently producing meaningless embeddings while
appearing to "work" (correct output shape, no exception). This is exactly the
failure mode HERMES.md's instruction to only pin a model if the adapter is
*genuinely* compatible with its contract is meant to catch.

The fix (see `docs/reports/M16.md`) made `input_mean`/`input_scale` configuration
(`EmbeddingConfig.input_mean`/`input_scale`, both new fields) rather than fixed
constants, defaulting to the original ArcFace-style values for backward
compatibility, with `config/jetson-full.yaml` setting them to SFace's actual
`(0.0, 1.0)`.

### How this was verified

Both SHA-256 values were confirmed independently, not copied from any pre-existing
note in this repository:

1. Both files are stored via Git LFS in `opencv_zoo`; `raw.githubusercontent.com`
   resolves to a small LFS *pointer* text file for both paths (confirmed directly
   -- see `scripts/provision-models.sh`'s header comment), not the real model
   bytes, so `media.githubusercontent.com` was used instead.
2. Each file was downloaded for real and hashed with `sha256sum`; both digests
   matched the `oid sha256:...` value recorded in the corresponding Git LFS
   pointer file at `opencv_zoo`'s `main` branch HEAD at verification time --
   i.e. two independent sources (the actual downloaded bytes, and the upstream
   repository's own LFS metadata) agree.
3. The downloaded SFace file was loaded for real with
   `cv2.dnn.readNetFromONNX()` and run through a forward pass, confirming a
   `(1, 128)` output shape.
4. The downloaded SFace file was run through the *fixed* `OnnxEmbeddingGenerator`
   (`input_mean=0.0`, `input_scale=1.0`) against a random 112x112x3 test image,
   producing a finite, unit-norm 128-dimension vector with no exception.
5. `config/jetson-full.yaml` was loaded through the real `create_app()` factory
   (with a temporary database path) and confirmed to wire a working
   `KnownPersonRecognizer`, `CandidateManager`, and `ActiveProfileSettingsApplier`
   using this exact model configuration -- see `docs/reports/M16.md`.

No accuracy/FAR/FRR evaluation against a real face dataset has been performed for
either model in this repository; only integrity, contract, and wiring were
verified. Real accuracy evaluation remains M5's open exit criterion.
