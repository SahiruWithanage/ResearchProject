"""Typed configuration schema (dataclasses matching the YAML structure)."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Type string + free-form params for one task generator. Resolved by the registry at build time."""
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """A source node's generator block. Only present on nodes with ``type: source``."""
    generator: GeneratorConfig


@dataclass(frozen=True, slots=True)
class NodeConfig:
    """Static config for one node from YAML (after node_profiles merging).

    Heterogeneity fields (Stage 4) all default to the homogeneous Phase 1
    behaviour, so old configs run unchanged.
    """
    id: str
    type: str
    cpu_capacity: float
    memory_capacity: float
    tier: str
    source: SourceConfig | None = (
        None  # required for source nodes, forbidden for helpers
    )
    cpu_speed: float = 1.0
    queue_limit: int | None = None
    accepts_task_types: tuple[str, ...] | None = None
    gpu_capacity: float = 0.0
    energy_cost_factor: float = 1.0


@dataclass(frozen=True, slots=True)
class AllocatorConfig:
    """Type string + free-form params for one allocator. Resolved by the registry at build time."""
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    """Static config for one controller from YAML. ``parent`` enables hierarchies later."""
    id: str
    allocator: AllocatorConfig
    manages: list[str]
    parent: str | None = None  # parent controller id, or None for a root


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Network / transmission model from YAML."""

    type: str
    default_profile: str = "instant"
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Where to write run outputs and how often to snapshot node state."""
    output_dir: str
    log_state_every: float = 1.0


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Top-level resolved config for one simulation run."""
    seed: int
    sim_duration: float
    dt: float
    controllers: list[ControllerConfig]
    nodes: list[NodeConfig]
    network: NetworkConfig
    logging: LoggingConfig
