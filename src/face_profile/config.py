"""Validated application configuration."""

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator
from yaml.constructor import ConstructorError


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class ConfigurationError(ValueError):
    """Raised when application configuration cannot be loaded safely."""


class StrictModel(BaseModel):
    """Base model that rejects coercion and unknown keys."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _path_from_yaml(value: object) -> object:
    if type(value) is str:
        return Path(value)
    return value


ConfigPath = Annotated[Path, BeforeValidator(_path_from_yaml)]


class CameraConfig(StrictModel):
    """Camera source and bounded recovery configuration."""

    enabled: bool = False
    source: Literal["mock", "webcam", "image", "video"] = "mock"
    path: ConfigPath | None = None
    device_index: int = Field(default=0, ge=0)
    retry_attempts: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_source_path(self) -> Self:
        if self.source in {"image", "video"} and self.path is None:
            raise ValueError("image and video sources require a path")
        if self.source in {"mock", "webcam"} and self.path is not None:
            raise ValueError("mock and webcam sources do not accept a path")
        if self.source != "webcam" and (self.device_index != 0 or self.retry_attempts != 2):
            raise ValueError("device index and retry attempts are webcam-only controls")
        return self


class DetectionConfig(StrictModel):
    """Profile-independent detector configuration and model-integrity gate."""

    enabled: bool = False
    backend: Literal["mock", "yunet"] = "mock"
    model_path: ConfigPath | None = None
    model_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    nms_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    top_k: int = Field(default=5000, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_backend_fields(self) -> Self:
        if self.backend == "yunet" and (self.model_path is None or self.model_sha256 is None):
            raise ValueError("YuNet requires a model path and SHA-256")
        if self.backend == "mock" and (
            self.model_path is not None or self.model_sha256 is not None
        ):
            raise ValueError("mock detector does not accept model configuration")
        if self.backend == "mock" and (
            self.confidence_threshold != 0.5 or self.nms_threshold != 0.3 or self.top_k != 5000
        ):
            raise ValueError("detector thresholds are YuNet-only controls")
        return self


class TrackingConfig(StrictModel):
    """Bounded, in-memory geometric tracking controls."""

    enabled: bool = False
    max_missing_frames: int = Field(default=5, ge=0, le=1_000)
    min_iou: float = Field(default=0.1, ge=0.0, le=1.0)
    max_center_distance: float = Field(default=2.0, gt=0.0, le=100.0)
    max_samples_per_track: int = Field(default=20, ge=1, le=1_000)
    max_tracks: int = Field(default=100, ge=1, le=10_000)


class QualityConfig(StrictModel):
    """Quality-filtering and alignment thresholds for M4 crop acceptance."""

    enabled: bool = False
    min_face_width: float = Field(default=40.0, gt=0.0, le=10_000.0)
    min_face_height: float = Field(default=40.0, gt=0.0, le=10_000.0)
    min_sharpness_variance: float = Field(default=60.0, ge=0.0, le=100_000.0)
    min_mean_brightness: float = Field(default=40.0, ge=0.0, le=255.0)
    max_mean_brightness: float = Field(default=215.0, ge=0.0, le=255.0)
    max_overexposed_fraction: float = Field(default=0.15, ge=0.0, le=1.0)
    max_roll_degrees: float = Field(default=25.0, ge=0.0, le=90.0)
    max_yaw_asymmetry: float = Field(default=0.35, ge=0.0, le=1.0)
    max_landmark_margin_violation: float = Field(default=0.05, ge=0.0, le=1.0)
    aligned_output_width: int = Field(default=112, ge=32, le=1024)
    aligned_output_height: int = Field(default=112, ge=32, le=1024)
    best_sample_max_tracks: int = Field(default=100, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.min_mean_brightness >= self.max_mean_brightness:
            raise ValueError("min_mean_brightness must be less than max_mean_brightness")
        return self


class EmbeddingConfig(StrictModel):
    """Embedding-generation and initial similarity threshold configuration."""

    enabled: bool = False
    backend: Literal["mock", "onnx"] = "mock"
    model_path: ConfigPath | None = None
    model_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_name: str = "mock-content-hash"
    model_version: str = "1"
    dimension: int = Field(default=128, ge=2, le=4096)
    input_width: int = Field(default=112, ge=32, le=1024)
    input_height: int = Field(default=112, ge=32, le=1024)
    # Defaults match the InsightFace/ArcFace-family convention this adapter
    # originally hardcoded ((pixel - 127.5) / 128). Other model families
    # (e.g. OpenCV Zoo's SFace, whose ``FaceRecognizerSF::feature`` uses
    # ``blobFromImage(img, 1, size, Scalar(0,0,0), swapRB=true)``, i.e. raw
    # unscaled pixels) require different values, so these are configurable
    # rather than fixed class constants.
    input_mean: float = Field(default=127.5, ge=0.0, le=255.0)
    input_scale: float = Field(default=1.0 / 128.0, gt=0.0, le=10.0)
    similarity_threshold: float = Field(default=0.5, ge=-1.0, le=1.0)
    similarity_margin: float = Field(default=0.05, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def validate_backend_fields(self) -> Self:
        if self.backend == "onnx" and (self.model_path is None or self.model_sha256 is None):
            raise ValueError("onnx embedding backend requires a model path and SHA-256")
        if self.backend == "mock" and (
            self.model_path is not None or self.model_sha256 is not None
        ):
            raise ValueError("mock embedding backend does not accept model configuration")
        if self.backend == "mock" and (
            self.model_name != "mock-content-hash"
            or self.model_version != "1"
            or self.dimension != 128
            or self.input_width != 112
            or self.input_height != 112
            or self.input_mean != 127.5
            or self.input_scale != 1.0 / 128.0
        ):
            raise ValueError("model identity and input-size fields are onnx-only controls")
        return self


class DatabaseConfig(StrictModel):
    """Encrypted, disabled-by-default persistent profile database configuration."""

    enabled: bool = False
    path: ConfigPath = Path("data/face_profile.sqlite3")
    key_path: ConfigPath = Path("data/face_profile.key")
    retention_days: int = Field(default=30, ge=1, le=3650)
    maximum_embeddings_per_profile: int = Field(default=50, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if self.path == self.key_path:
            raise ValueError("database path and key path must differ")
        return self


class RecognitionConfig(StrictModel):
    """Temporal-confirmation controls for M7 known-person recognition."""

    enabled: bool = False
    min_consistent_observations: int = Field(default=3, ge=1, le=100)
    confirmation_window_seconds: float = Field(default=5.0, gt=0.0, le=600.0)
    max_tracks: int = Field(default=100, ge=1, le=10_000)
    embedding_cache_refresh_seconds: float = Field(default=2.0, gt=0.0, le=600.0)


class EnrollmentConfig(StrictModel):
    """M8 candidate enrollment thresholds and safety gates.

    Automatic promotion remains disabled by default and requires an explicit
    operator acknowledgement. Cross-feature prerequisites are enforced by
    :class:`AppConfig`, where every relevant section is visible.
    """

    enabled: bool = False
    automatic_promotion: bool = False
    automatic_promotion_consent_acknowledged: bool = False
    minimum_samples: int = Field(default=5, ge=1, le=100)
    minimum_observation_seconds: float = Field(default=3.0, gt=0.0, le=600.0)
    minimum_quality: float = Field(default=0.65, ge=0.0, le=1.0)
    candidate_expiry_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    maximum_samples_per_candidate: int = Field(default=50, ge=1, le=1000)
    default_name_prefix: str = Field(default="Unknown", min_length=1, max_length=64)
    minimum_internal_consistency: float = Field(default=0.5, ge=-1.0, le=1.0)
    maximum_internal_similarity: float = Field(default=0.9999, ge=-1.0, le=1.0)
    duplicate_profile_threshold: float = Field(default=0.5, ge=-1.0, le=1.0)
    duplicate_candidate_threshold: float = Field(default=0.5, ge=-1.0, le=1.0)
    near_frontal_max_roll_degrees: float = Field(default=12.0, ge=0.0, le=90.0)
    near_frontal_max_yaw_asymmetry: float = Field(default=0.15, ge=0.0, le=1.0)
    candidate_retention_days: int = Field(default=7, ge=1, le=3650)


class ActiveUserConfig(StrictModel):
    """M10 active-user scoring, hysteresis, and switch-cooldown configuration."""

    enabled: bool = False
    face_size_weight: float = Field(default=0.3, ge=0.0, le=10.0)
    centre_weight: float = Field(default=0.2, ge=0.0, le=10.0)
    duration_weight: float = Field(default=0.2, ge=0.0, le=10.0)
    confidence_weight: float = Field(default=0.2, ge=0.0, le=10.0)
    priority_weight: float = Field(default=0.1, ge=0.0, le=10.0)
    duration_saturation_seconds: float = Field(default=5.0, gt=0.0, le=600.0)
    priority_normalization_scale: float = Field(default=100.0, gt=0.0, le=100_000.0)
    switch_margin: float = Field(default=0.1, ge=0.0, le=10.0)
    stability_duration_seconds: float = Field(default=2.0, gt=0.0, le=600.0)
    switch_cooldown_seconds: float = Field(default=5.0, ge=0.0, le=600.0)
    min_visible_duration_seconds: float = Field(default=0.5, ge=0.0, le=600.0)
    leaving_grace_seconds: float = Field(default=1.0, ge=0.0, le=600.0)


class PreferenceLearningConfig(StrictModel):
    """M11 last-used-preference attribution and debounce configuration."""

    enabled: bool = False
    debounce_seconds: float = Field(default=3.0, gt=0.0, le=600.0)
    min_active_duration_seconds: float = Field(default=2.0, ge=0.0, le=600.0)
    self_application_tolerance_seconds: float = Field(default=2.0, ge=0.0, le=60.0)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class APIConfig(StrictModel):
    """M12 headless daemon/REST/WebSocket API configuration.

    Fails closed at configuration-validation time (not merely at request
    time) when the API is enabled, bound beyond loopback, and no
    ``auth_token`` is configured, per HERMES.md's non-loopback
    administrative-API rule.
    """

    enabled: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8443, ge=1, le=65535)
    auth_token: str | None = Field(default=None, min_length=16, max_length=256)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    max_request_body_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)
    events_page_size: int = Field(default=50, ge=1, le=1000)
    worker_poll_interval_seconds: float = Field(default=0.1, gt=0.0, le=60.0)
    detection_interval_frames: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def validate_fail_closed(self) -> Self:
        if self.enabled and self.bind_host not in _LOOPBACK_HOSTS and self.auth_token is None:
            raise ValueError(
                "non-loopback API binding requires an explicit auth_token; "
                "refusing to start without authentication per HERMES.md"
            )
        return self


class UIConfig(StrictModel):
    """M13 optional dashboard: a static page served by the M12 API, disabled by default.

    ``webcam_preview_enabled`` gates a separate, additional capability: a
    bounded-rate, server-annotated JPEG snapshot of the pipeline worker's
    current frame (boxes and privacy-safe labels only, never a raw stream).
    It defaults to False independently of ``enabled`` -- the dashboard can
    be on with the preview still off -- per ARCHITECTURE.md §13 ("do not
    expose raw camera streams by default"). Only config/jetson-full.yaml
    turns this on; every other tracked config keeps it False.
    """

    enabled: bool = False
    webcam_preview_enabled: bool = False
    webcam_preview_jpeg_quality: int = Field(default=70, ge=1, le=100)
    webcam_preview_max_fps: float = Field(default=5.0, gt=0.0, le=30.0)
    webcam_preview_max_width: int = Field(default=640, ge=64, le=4096)


class LivenessConfig(StrictModel):
    """M14 passive/active liveness thresholds, disabled by default.

    The passive checks are classical spectral/reflectance heuristics, not
    a trained anti-spoof model; see ``liveness/passive.py`` and
    docs/reports/M14.md for the documented, unevaluated-accuracy caveat.
    """

    enabled: bool = False
    minimum_passive_score: float = Field(default=0.5, ge=0.0, le=1.0)
    high_frequency_energy_min: float = Field(default=0.02, ge=0.0, le=1.0)
    high_frequency_energy_max: float = Field(default=0.35, ge=0.0, le=1.0)
    minimum_specular_variance: float = Field(default=5.0, ge=0.0, le=10_000.0)
    require_active_challenge: bool = False
    active_challenge_min_yaw_delta: float = Field(default=0.2, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.high_frequency_energy_min >= self.high_frequency_energy_max:
            raise ValueError(
                "high_frequency_energy_min must be less than high_frequency_energy_max"
            )
        return self


class LoggingConfig(StrictModel):
    """Structured logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class SettingsConfig(StrictModel):
    """Settings-adapter selection, initial mock values, and rate limiting.

    M9's real Linux adapter and M17's real Windows adapter must be
    explicitly selected; ``mock`` remains the default so recognition and
    profile code never changes host settings unless a deployer opts in.
    This model only validates the *value*; ``settings/factory.py`` adds a
    fail-closed *runtime* platform guard (``linux`` refused off Linux,
    ``windows`` refused off Windows) since a YAML-only validator cannot
    see which OS is actually running it.
    """

    backend: Literal["mock", "linux", "windows"] = "mock"
    volume: int = Field(default=50, ge=0, le=100)
    brightness: int = Field(default=50, ge=0, le=100)
    min_apply_interval_seconds: float = Field(default=0.2, ge=0.0, le=60.0)


class AppConfig(StrictModel):
    """Root application configuration."""

    camera: CameraConfig = CameraConfig()
    detection: DetectionConfig = DetectionConfig()
    tracking: TrackingConfig = TrackingConfig()
    quality: QualityConfig = QualityConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    database: DatabaseConfig = DatabaseConfig()
    recognition: RecognitionConfig = RecognitionConfig()
    enrollment: EnrollmentConfig = EnrollmentConfig()
    active_user: ActiveUserConfig = ActiveUserConfig()
    preference_learning: PreferenceLearningConfig = PreferenceLearningConfig()
    api: APIConfig = APIConfig()
    ui: UIConfig = UIConfig()
    liveness: LivenessConfig = LivenessConfig()
    logging: LoggingConfig = LoggingConfig()
    settings: SettingsConfig = SettingsConfig()

    @model_validator(mode="after")
    def validate_automatic_promotion_gates(self) -> Self:
        """Fail closed unless automatic biometric enrollment is fully explicit."""

        if not self.enrollment.automatic_promotion:
            return self
        if not self.enrollment.automatic_promotion_consent_acknowledged:
            raise ValueError(
                "automatic_promotion requires automatic_promotion_consent_acknowledged: true"
            )
        required = {
            "camera": self.camera.enabled,
            "detection": self.detection.enabled,
            "tracking": self.tracking.enabled,
            "quality": self.quality.enabled,
            "embedding": self.embedding.enabled,
            "database": self.database.enabled,
            "recognition": self.recognition.enabled,
            "enrollment": self.enrollment.enabled,
            "api": self.api.enabled,
            "liveness": self.liveness.enabled,
        }
        missing = sorted(name for name, enabled in required.items() if not enabled)
        if missing:
            raise ValueError(
                "automatic_promotion requires enabled safety/runtime features: "
                + ", ".join(missing)
            )
        return self


def load_config(path: Path) -> AppConfig:
    """Load and validate an application YAML file."""

    if not path.is_file():
        raise ConfigurationError(f"configuration file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.load(stream, Loader=UniqueKeyLoader)
        return AppConfig.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"invalid configuration: {path}") from exc
