"""Validated application configuration."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
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


class CameraConfig(StrictModel):
    """Camera startup configuration."""

    enabled: bool = False


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
