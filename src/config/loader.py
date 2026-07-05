"""YAML configuration loading and validation.

Reads a YAML config into a typed SimulationConfig while checking both
per-field constraints and cross-references. If anything's wrong, raises
ConfigError with a message that says exactly which field is bad and why.

What this loader checks:

- Required top-level fields are present, with the right types.
- sim_duration > 0 and dt > 0.
- At least 2 nodes and at least 1 controller.
- Node IDs and controller IDs are unique.
- node.type is one of "source" or "helper".
- Source nodes have a `source` block, helper nodes don't.
- Each controller.manages entry refers to a real node ID.
- Each controller.parent (if not null) refers to a real controller ID.
- Every node is managed by exactly one controller.

What this loader does NOT check:

- Whether a generator/allocator `type` string is actually registered. That
  happens later, when the Environment builder asks the factory to resolve
  the class.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    AllocatorConfig,
    ControllerConfig,
    GeneratorConfig,
    LoggingConfig,
    NetworkConfig,
    NodeConfig,
    SimulationConfig,
    SourceConfig,
)


class ConfigError(ValueError):
    """Raised when a configuration file is malformed or inconsistent."""


VALID_NODE_TYPES = frozenset({"source", "helper"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> SimulationConfig:
    """Load a YAML config file from disk and return a validated config."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file does not exist: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ConfigError(f"config file is empty: {path}")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"top-level YAML must be a mapping, got {type(raw).__name__}")
    return parse_config(raw)


def parse_config(raw: Mapping[str, Any]) -> SimulationConfig:
    """Parse and validate an already-loaded raw config dict."""
    seed = _require_int(raw, "seed")
    sim_duration = float(_require_number(raw, "sim_duration"))
    dt = float(_require_number(raw, "dt"))

    if sim_duration <= 0:
        raise ConfigError(f"sim_duration must be > 0, got {sim_duration}")
    if dt <= 0:
        raise ConfigError(f"dt must be > 0, got {dt}")

    raw_nodes = _require_list(raw, "nodes")
    if len(raw_nodes) < 2:
        raise ConfigError(f"nodes must have at least 2 entries, got {len(raw_nodes)}")
    nodes = [_parse_node(n, idx) for idx, n in enumerate(raw_nodes)]
    _check_unique_ids(nodes, "node")

    raw_controllers = _require_list(raw, "controllers")
    if len(raw_controllers) < 1:
        raise ConfigError("controllers must have at least 1 entry")
    controllers = [_parse_controller(c, idx) for idx, c in enumerate(raw_controllers)]
    _check_unique_ids(controllers, "controller")

    network_cfg = _parse_network(raw.get("network"))
    logging_cfg = _parse_logging(_require_mapping(raw, "logging"))

    _check_cross_references(controllers, nodes)

    return SimulationConfig(
        seed=seed,
        sim_duration=sim_duration,
        dt=dt,
        controllers=controllers,
        nodes=nodes,
        network=network_cfg,
        logging=logging_cfg,
    )


# ---------------------------------------------------------------------------
# Per-section parsing
# ---------------------------------------------------------------------------


def _parse_node(raw: Any, idx: int) -> NodeConfig:
    """Parse one node block: validate type, capacities, and source/helper rules."""
    where = f"nodes[{idx}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} must be a mapping, got {type(raw).__name__}")

    node_id = _require_str(raw, "id", where)
    node_type = _require_str(raw, "type", where)
    if node_type not in VALID_NODE_TYPES:
        raise ConfigError(
            f"{where}.type must be one of {sorted(VALID_NODE_TYPES)}, "
            f"got {node_type!r}"
        )
    cpu_capacity = float(_require_number(raw, "cpu_capacity", where))
    memory_capacity = float(_require_number(raw, "memory_capacity", where))
    tier = _require_str(raw, "tier", where)

    if cpu_capacity <= 0:
        raise ConfigError(f"{where}.cpu_capacity must be > 0, got {cpu_capacity}")
    if memory_capacity <= 0:
        raise ConfigError(f"{where}.memory_capacity must be > 0, got {memory_capacity}")

    raw_source = raw.get("source")
    if node_type == "source":
        if raw_source is None:
            raise ConfigError(
                f"{where} has type 'source' but is missing a 'source' block"
            )
        source = _parse_source(raw_source, f"{where}.source")
    else:  # helper
        if raw_source is not None:
            raise ConfigError(
                f"{where} has type 'helper' but also defines a 'source' block; "
                f"helper nodes must not have sources"
            )
        source = None

    return NodeConfig(
        id=node_id,
        type=node_type,
        cpu_capacity=cpu_capacity,
        memory_capacity=memory_capacity,
        tier=tier,
        source=source,
    )


def _parse_source(raw: Any, where: str) -> SourceConfig:
    """Parse a source block. Currently just wraps a generator sub-block."""
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} must be a mapping, got {type(raw).__name__}")
    raw_gen = _require_mapping(raw, "generator", where)
    return SourceConfig(generator=_parse_generator(raw_gen, f"{where}.generator"))


def _parse_generator(raw: Mapping[str, Any], where: str) -> GeneratorConfig:
    """Parse a generator block: pull out `type`, bundle the rest as `params`."""
    gen_type = _require_str(raw, "type", where)
    params = {k: v for k, v in raw.items() if k != "type"}
    return GeneratorConfig(type=gen_type, params=params)


def _parse_allocator(raw: Any, where: str) -> AllocatorConfig:
    """Parse an allocator block: pull out `type`, bundle the rest as `params`."""
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} must be a mapping, got {type(raw).__name__}")
    alloc_type = _require_str(raw, "type", where)
    params = {k: v for k, v in raw.items() if k != "type"}
    return AllocatorConfig(type=alloc_type, params=params)


def _parse_controller(raw: Any, idx: int) -> ControllerConfig:
    """Parse one controller block: id, allocator, manages list, optional parent."""
    where = f"controllers[{idx}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} must be a mapping, got {type(raw).__name__}")

    ctrl_id = _require_str(raw, "id", where)
    allocator = _parse_allocator(
        _require_mapping(raw, "allocator", where), f"{where}.allocator"
    )
    manages = _require_list(raw, "manages", where)
    for i, m in enumerate(manages):
        if not isinstance(m, str):
            raise ConfigError(
                f"{where}.manages[{i}] must be a string, got "
                f"{type(m).__name__}: {m!r}"
            )

    parent_raw = raw.get("parent")
    if parent_raw is not None and not isinstance(parent_raw, str):
        raise ConfigError(
            f"{where}.parent must be a string or null, got "
            f"{type(parent_raw).__name__}: {parent_raw!r}"
        )

    return ControllerConfig(
        id=ctrl_id,
        allocator=allocator,
        manages=list(manages),
        parent=parent_raw,
    )


def _parse_network(raw: Any) -> NetworkConfig:
    """Parse optional network block; default to instant (zero delay)."""
    if raw is None:
        return NetworkConfig(type="instant", default_profile="instant")
    if not isinstance(raw, Mapping):
        raise ConfigError(
            f"network must be a mapping, got {type(raw).__name__}"
        )
    net_type = _require_str(raw, "type", "network")
    default_profile = str(raw.get("default_profile", "wifi"))
    profiles_raw = raw.get("profiles", {})
    if profiles_raw is None:
        profiles_raw = {}
    if not isinstance(profiles_raw, Mapping):
        raise ConfigError("network.profiles must be a mapping")
    profiles: dict[str, dict[str, Any]] = {}
    for name, spec in profiles_raw.items():
        if not isinstance(spec, Mapping):
            raise ConfigError(
                f"network.profiles[{name!r}] must be a mapping"
            )
        profiles[str(name)] = dict(spec)

    links_raw = raw.get("links", [])
    if links_raw is None:
        links_raw = []
    if not isinstance(links_raw, list):
        raise ConfigError("network.links must be a list")
    links: list[dict[str, Any]] = []
    for i, link in enumerate(links_raw):
        if not isinstance(link, Mapping):
            raise ConfigError(
                f"network.links[{i}] must be a mapping, got {type(link).__name__}"
            )
        if "from" not in link or "to" not in link:
            raise ConfigError(f"network.links[{i}] requires 'from' and 'to'")
        links.append(dict(link))

    params_raw = raw.get("params", {})
    if params_raw is None:
        params_raw = {}
    if not isinstance(params_raw, Mapping):
        raise ConfigError("network.params must be a mapping")

    return NetworkConfig(
        type=net_type,
        default_profile=default_profile,
        profiles=profiles,
        links=links,
        params=dict(params_raw),
    )


def _parse_logging(raw: Mapping[str, Any]) -> LoggingConfig:
    """Parse the logging block: output_dir and log_state_every."""
    where = "logging"
    output_dir = _require_str(raw, "output_dir", where)
    log_every = raw.get("log_state_every", 1.0)
    if not _is_number(log_every):
        raise ConfigError(
            f"{where}.log_state_every must be a number, got "
            f"{type(log_every).__name__}: {log_every!r}"
        )
    log_every = float(log_every)
    if log_every <= 0:
        raise ConfigError(f"{where}.log_state_every must be > 0, got {log_every}")
    return LoggingConfig(output_dir=output_dir, log_state_every=log_every)


# ---------------------------------------------------------------------------
# Cross-reference validation
# ---------------------------------------------------------------------------


def _check_cross_references(
    controllers: list[ControllerConfig], nodes: list[NodeConfig]
) -> None:
    """Verify manages-list node IDs, parent IDs, and exactly-one-controller coverage."""
    node_ids = {n.id for n in nodes}
    controller_ids = {c.id for c in controllers}

    # Each managed entry must refer to a real node ID (Phase 1: nodes only).
    managed_by: dict[str, str] = {}
    for ctrl in controllers:
        for managed in ctrl.manages:
            if managed not in node_ids:
                raise ConfigError(
                    f"controller {ctrl.id!r} manages unknown node {managed!r}. "
                    f"known nodes: {sorted(node_ids)}"
                )
            if managed in managed_by:
                raise ConfigError(
                    f"node {managed!r} is managed by multiple controllers: "
                    f"{managed_by[managed]!r} and {ctrl.id!r}"
                )
            managed_by[managed] = ctrl.id

        if ctrl.parent is not None and ctrl.parent not in controller_ids:
            raise ConfigError(
                f"controller {ctrl.id!r} declares parent {ctrl.parent!r} "
                f"which is not a defined controller. "
                f"known controllers: {sorted(controller_ids)}"
            )
        if ctrl.parent == ctrl.id:
            raise ConfigError(f"controller {ctrl.id!r} cannot be its own parent")

    # Every node must be managed by some controller.
    unmanaged = node_ids - managed_by.keys()
    if unmanaged:
        raise ConfigError(
            f"the following nodes are not managed by any controller: "
            f"{sorted(unmanaged)}"
        )


# ---------------------------------------------------------------------------
# Field-extraction helpers
# ---------------------------------------------------------------------------


def _require(raw: Mapping[str, Any], key: str, where: str = "<root>") -> Any:
    if key not in raw:
        raise ConfigError(f"{where} is missing required field {key!r}")
    return raw[key]


def _require_int(raw: Mapping[str, Any], key: str, where: str = "<root>") -> int:
    value = _require(raw, key, where)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"{where}.{key} must be int, got {type(value).__name__}: {value!r}"
        )
    return value


def _require_number(
    raw: Mapping[str, Any], key: str, where: str = "<root>"
) -> float | int:
    value = _require(raw, key, where)
    if not _is_number(value):
        raise ConfigError(
            f"{where}.{key} must be a number, got " f"{type(value).__name__}: {value!r}"
        )
    return value


def _require_str(raw: Mapping[str, Any], key: str, where: str = "<root>") -> str:
    value = _require(raw, key, where)
    if not isinstance(value, str):
        raise ConfigError(
            f"{where}.{key} must be a string, got " f"{type(value).__name__}: {value!r}"
        )
    return value


def _require_list(raw: Mapping[str, Any], key: str, where: str = "<root>") -> list[Any]:
    value = _require(raw, key, where)
    if not isinstance(value, list):
        raise ConfigError(
            f"{where}.{key} must be a list, got {type(value).__name__}: {value!r}"
        )
    return value


def _require_mapping(
    raw: Mapping[str, Any], key: str, where: str = "<root>"
) -> Mapping[str, Any]:
    value = _require(raw, key, where)
    if not isinstance(value, Mapping):
        raise ConfigError(
            f"{where}.{key} must be a mapping, got "
            f"{type(value).__name__}: {value!r}"
        )
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_unique_ids(items: list[Any], kind: str) -> None:
    """Raise ConfigError if any two items share the same `.id` attribute."""
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ConfigError(f"duplicate {kind} id: {item.id!r}")
        seen.add(item.id)
