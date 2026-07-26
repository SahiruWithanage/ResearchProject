"""Registry -> form schema: the UI asks the code what options exist.

Every plugin registers itself with explicit keyword parameters and
defaults, so `inspect.signature` is the single source of truth for the
UI's forms. Register a new allocator (or generator, network model, ...)
and it appears in the UI with its parameters — no UI edits.

Two wrinkles handled here:
- some parameters are injected by the Environment, never user-set
  (generator `rng`/`source_node_id`, network `rng`/`variation_entropy`,
  and the network block fields that have dedicated YAML sections);
- `varying_fluid_link`/`trace_fluid_link` forward `**fluid_kwargs` to
  their parent, so a `**kwargs` catch-all unions in the parent's
  signature.
"""

from __future__ import annotations

import inspect
import math
from typing import Any

import src.simulation.environment  # noqa: F401 — triggers every @register
from src.config.factory import (
    Registry,
    allocators,
    distributions,
    generators,
    network_models,
    observability_models,
    rate_patterns,
    scenarios,
)

# Private core constants mirrored on purpose: importing them (rather than
# copying values) keeps the UI in lockstep with the loader and profiles.
from src.config.loader import _PROFILE_FIELDS
from src.network.fluid_link import _BUILTIN_PROFILES

# Parameters the Environment injects at build time — never user-editable.
_HIDDEN_PARAMS: dict[str, frozenset[str]] = {
    "generators": frozenset({"source_node_id", "rng"}),
    # default_profile / profiles / links live in the dedicated `network:`
    # YAML section, not in `network.params`.
    "network_models": frozenset(
        {"rng", "default_profile", "profiles", "links", "variation_entropy"}
    ),
}

# Generator fields that accept a number OR a `{dist: ...}` spec, and the
# field that accepts a number OR a `{pattern: ...}` spec. The frontend
# offers the distribution / rate-pattern widget on exactly these.
_DIST_CAPABLE_FIELDS = [
    "cpu_demand",
    "memory_demand",
    "data_size",
    "result_size",
    "gpu_demand",
    "deadline_offset",
]
_RATE_PATTERN_FIELDS = ["rate"]

_TYPE_NAMES = {
    "float": "number",
    "int": "integer",
    "bool": "boolean",
    "str": "string",
    "list": "array",
    "dict": "object",
}


def _jsonable(value: Any) -> Any:
    """Defaults must survive JSON encoding (JS chokes on Infinity/NaN)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def _param_type(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "any"
    text = str(annotation).replace(" ", "")
    parts = [p for p in text.split("|") if p not in ("None", "NoneType")]
    base = parts[0] if parts else "any"
    base = base.split("[", 1)[0].rsplit(".", 1)[-1]
    return _TYPE_NAMES.get(base, "any")


def _init_owners(cls: type) -> list[type]:
    """The classes whose __init__ signatures apply to `cls`.

    Nearest __init__ in the MRO first; parents are appended only while a
    `**kwargs` catch-all forwards to them (otherwise the parent's params
    would not actually be accepted).
    """
    chain = [k for k in cls.__mro__ if k is not object and "__init__" in k.__dict__]
    if not chain:
        return []
    owners = [chain[0]]
    idx = 0
    while idx < len(owners):
        sig = inspect.signature(owners[idx].__init__)
        forwards = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        pos = chain.index(owners[idx])
        if forwards and pos + 1 < len(chain):
            owners.append(chain[pos + 1])
        idx += 1
    return owners


def _class_params(cls: type, hidden: frozenset[str]) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    # Parents first so the subclass's own definition wins on name clashes.
    for owner in reversed(_init_owners(cls)):
        sig = inspect.signature(owner.__init__)
        for name, p in sig.parameters.items():
            if name == "self" or name in hidden:
                continue
            if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                continue
            entry: dict[str, Any] = {
                "type": _param_type(p.annotation),
                "required": p.default is inspect.Parameter.empty,
            }
            if p.default is not inspect.Parameter.empty:
                entry["default"] = _jsonable(p.default)
            params[name] = entry
    return params


def _registry_schema(
    registry: Registry, hidden: frozenset[str] = frozenset()
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in registry.names():
        cls = registry.get(name)
        doc = (cls.__doc__ or "").strip().splitlines()
        out[name] = {
            "doc": doc[0] if doc else "",
            "params": _class_params(cls, hidden),
        }
    return out


def build_schema() -> dict[str, Any]:
    """Everything the frontend needs to render every form, in one payload."""
    return {
        "registries": {
            "generators": _registry_schema(generators, _HIDDEN_PARAMS["generators"]),
            "allocators": _registry_schema(allocators),
            "network_models": _registry_schema(
                network_models, _HIDDEN_PARAMS["network_models"]
            ),
            "distributions": _registry_schema(distributions),
            "rate_patterns": _registry_schema(rate_patterns),
            "observability_models": _registry_schema(observability_models),
            "scenarios": _registry_schema(scenarios),
        },
        "network_profiles": _jsonable(_BUILTIN_PROFILES),
        "node_fields": sorted(_PROFILE_FIELDS),
        "dist_capable_fields": _DIST_CAPABLE_FIELDS,
        "rate_pattern_fields": _RATE_PATTERN_FIELDS,
    }
