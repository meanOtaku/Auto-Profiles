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
        ):
            raise ValueError("model identity and input-size fields are onnx-only controls")
        return self


class DatabaseConfig(StrictModel):
    """Encrypted, disabled-by-default persistent profile database configuration."""

    enabled: bool = False
    path: ConfigPath = Path("data/face_profile.sqlite3")
    key_path: ConfigPath = Path("data/face_profile.key")
    retention_days: int = Field(default=30, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if self.path == self.key_path:
            raise ValueError("database path and key path must differ")
        return self


class LoggingConfig(StrictModel):
    """Structured logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class SettingsConfig(StrictModel):
    """Safe initial values used by the mock settings adapter."""

    volume: int = Field(default=50, ge=0, le=100)
    brightness: int = Field(default=50, ge=0, le=100)


class AppConfig(StrictModel):
    """Root application configuration."""

    camera: CameraConfig = CameraConfig()
    detection: DetectionConfig = DetectionConfig()
    tracking: TrackingConfig = TrackingConfig()
    quality: QualityConfig = QualityConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()
    settings: SettingsConfig = SettingsConfig()


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
