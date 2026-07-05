"""Fluid link model: bandwidth + base latency + optional jitter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from src.config.factory import network_models
from src.models import Task
from src.network.base import NetworkModel

# Built-in profile defaults (bytes/s, seconds). Override via YAML.
_BUILTIN_PROFILES: dict[str, dict[str, float]] = {
    "lan": {"bandwidth_bps": 1.0e9, "base_latency_s": 0.001, "jitter_s": 0.0},
    "wifi": {"bandwidth_bps": 50.0e6, "base_latency_s": 0.010, "jitter_s": 0.0},
    "5g": {"bandwidth_bps": 100.0e6, "base_latency_s": 0.020, "jitter_s": 0.0},
    "instant": {"bandwidth_bps": float("inf"), "base_latency_s": 0.0, "jitter_s": 0.0},
    "custom": {"bandwidth_bps": 1.0e9, "base_latency_s": 0.001, "jitter_s": 0.0},
}


@dataclass(frozen=True, slots=True)
class _LinkSpec:
    bandwidth_bps: float
    base_latency_s: float
    jitter_s: float


@network_models.register("fluid_link")
class FluidLinkNetworkModel(NetworkModel):
    """One-way delay = base_latency + data_size/bandwidth + jitter sample.

    Args:
        default_profile: Profile name for links without an explicit override.
        profiles: Optional YAML overrides per profile name.
        links: Optional list of dicts with ``from``, ``to``, and either
            ``profile`` or explicit ``bandwidth_bps`` / ``base_latency_s``.
        rng: Seeded generator for jitter (required if any jitter > 0).
    """

    def __init__(
        self,
        *,
        default_profile: str = "wifi",
        profiles: dict[str, Any] | None = None,
        links: list[dict[str, Any]] | None = None,
        rng: np.random.Generator | None = None,
    ) -> None:
        self.default_profile = default_profile
        self._rng = rng
        self._pair_specs: dict[tuple[str, str], _LinkSpec] = {}
        self._profile_specs = self._build_profile_specs(profiles or {})
        for raw in links or []:
            self._register_link(raw)

    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        if source_id == target_id:
            return 0.0
        spec = self._resolve_spec(source_id, target_id)
        transfer = 0.0 if spec.bandwidth_bps == float("inf") else (
            task.data_size / spec.bandwidth_bps
        )
        jitter = self._sample_jitter(spec.jitter_s)
        return max(0.0, spec.base_latency_s + transfer + jitter)

    def _resolve_spec(self, source_id: str, target_id: str) -> _LinkSpec:
        key = (source_id, target_id)
        if key in self._pair_specs:
            return self._pair_specs[key]
        return self._profile_specs[self.default_profile]

    def _register_link(self, raw: dict[str, Any]) -> None:
        from_id = str(raw["from"])
        to_id = str(raw["to"])
        if "profile" in raw:
            base = self._profile_specs[str(raw["profile"])]
            spec = _LinkSpec(
                bandwidth_bps=float(raw.get("bandwidth_bps", base.bandwidth_bps)),
                base_latency_s=float(
                    raw.get("base_latency_s", base.base_latency_s)
                ),
                jitter_s=float(raw.get("jitter_s", base.jitter_s)),
            )
        else:
            spec = _LinkSpec(
                bandwidth_bps=float(raw["bandwidth_bps"]),
                base_latency_s=float(raw["base_latency_s"]),
                jitter_s=float(raw.get("jitter_s", 0.0)),
            )
        self._pair_specs[(from_id, to_id)] = spec

    def _build_profile_specs(
        self, overrides: dict[str, Any]
    ) -> dict[str, _LinkSpec]:
        specs: dict[str, _LinkSpec] = {}
        names = set(_BUILTIN_PROFILES) | set(overrides)
        for name in names:
            base = dict(_BUILTIN_PROFILES.get(name, _BUILTIN_PROFILES["custom"]))
            if name in overrides and isinstance(overrides[name], dict):
                base.update(overrides[name])
            specs[name] = _LinkSpec(
                bandwidth_bps=float(base.get("bandwidth_bps", 1.0e9)),
                base_latency_s=float(base.get("base_latency_s", 0.0)),
                jitter_s=float(base.get("jitter_s", 0.0)),
            )
        if "custom" not in specs:
            specs["custom"] = _LinkSpec(
                bandwidth_bps=1.0e9,
                base_latency_s=0.0,
                jitter_s=0.0,
            )
        if self.default_profile not in specs:
            raise ValueError(
                f"unknown default_profile {self.default_profile!r}; "
                f"known profiles: {sorted(specs)}"
            )
        return specs

    def _sample_jitter(self, jitter_s: float) -> float:
        if jitter_s <= 0.0:
            return 0.0
        if self._rng is None:
            raise ValueError(
                "fluid_link network with jitter requires a seeded rng"
            )
        return float(self._rng.uniform(-jitter_s, jitter_s))
