"""Registry -> form schema: the UI asks the code what options exist.

Every plugin registers itself with explicit keyword parameters and
defaults, so `inspect.signature` is the single source of truth for the
UI's forms. Register a new allocator (or generator, network model, ...)
and it appears in the UI with its parameters - no UI edits.

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

import src.simulation.environment  # noqa: F401 - triggers every @register
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
from src.generation.task_builder import TaskBuilder
from src.network.fluid_link import _BUILTIN_PROFILES
from src.network.trace_fluid_link import _BandwidthTrace
from ui.help import FIELD_HELP, param_help, plugin_help

# Parameters the Environment injects at build time - never user-editable.
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


def _class_params(
    cls: type,
    hidden: frozenset[str],
    registry: str | None = None,
    plugin: str | None = None,
) -> dict[str, dict[str, Any]]:
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
            text = param_help(registry, plugin, name)
            if text:
                entry["help"] = text
            params[name] = entry
    return params


def _registry_schema(
    registry: Registry,
    hidden: frozenset[str] = frozenset(),
    kind: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in registry.names():
        cls = registry.get(name)
        doc = (cls.__doc__ or "").strip().splitlines()
        out[name] = {
            # `doc` is the developer docstring; `help` is the plain-language
            # description the UI actually shows.
            "doc": doc[0] if doc else "",
            "help": plugin_help(kind or "", name),
            "params": _class_params(cls, hidden, kind, name),
        }
    return out


def _composite_schemas() -> dict[str, Any]:
    """Item schemas for list-valued params, introspected from the classes
    that consume the items - so list editors are not hardcoded either.

    Keyed registry -> plugin ("*" = any plugin of that registry) -> param.
    """
    mix_item = _class_params(
        TaskBuilder, frozenset({"source_node_id", "task_mix"}), "generators", "*"
    )
    mix_item = {
        "weight": {
            "type": "number",
            "required": True,
            "help": param_help("generators", "*", "weight"),
        },
        **mix_item,
    }
    trace_item = _class_params(
        _BandwidthTrace, frozenset(), "network_models", "trace_fluid_link"
    )
    trace_item = {
        "from": {"type": "node_id", "required": True, "help": param_help(None, None, "from")},
        "to": {"type": "node_id", "required": True, "help": param_help(None, None, "to")},
        **trace_item,
    }
    return {
        "generators": {"*": {"task_mix": mix_item}},
        "network_models": {"trace_fluid_link": {"traces": trace_item}},
    }


def build_schema() -> dict[str, Any]:
    """Everything the frontend needs to render every form, in one payload."""
    return {
        "registries": {
            "generators": _registry_schema(
                generators, _HIDDEN_PARAMS["generators"], "generators"
            ),
            "allocators": _registry_schema(allocators, kind="allocators"),
            "network_models": _registry_schema(
                network_models, _HIDDEN_PARAMS["network_models"], "network_models"
            ),
            "distributions": _registry_schema(distributions, kind="distributions"),
            "rate_patterns": _registry_schema(rate_patterns, kind="rate_patterns"),
            "observability_models": _registry_schema(
                observability_models, kind="observability_models"
            ),
            "scenarios": _registry_schema(scenarios, kind="scenarios"),
        },
        "field_help": FIELD_HELP,
        "network_profiles": _jsonable(_BUILTIN_PROFILES),
        "node_fields": sorted(_PROFILE_FIELDS),
        "dist_capable_fields": _DIST_CAPABLE_FIELDS,
        "rate_pattern_fields": _RATE_PATTERN_FIELDS,
        "composites": _composite_schemas(),
    }
