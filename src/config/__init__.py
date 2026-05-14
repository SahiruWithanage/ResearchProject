"""Configuration: YAML loader, validation, and plugin registries."""

from .factory import Registry, allocators, generators
from .loader import ConfigError, load_config, parse_config
from .schema import (
    AllocatorConfig,
    ControllerConfig,
    GeneratorConfig,
    LoggingConfig,
    NodeConfig,
    SimulationConfig,
    SourceConfig,
)

__all__ = [
    "AllocatorConfig",
    "ControllerConfig",
    "GeneratorConfig",
    "LoggingConfig",
    "NodeConfig",
    "SimulationConfig",
    "SourceConfig",
    "ConfigError",
    "load_config",
    "parse_config",
    "Registry",
    "allocators",
    "generators",
]
