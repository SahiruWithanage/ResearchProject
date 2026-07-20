"""Fluid link model: bandwidth + base latency + jitter, uplink and downlink.

Units (pinned; see resources/DELAY_MODEL.md):
    - ``Task.data_size`` (uplink payload) and ``Task.result_size``
      (downlink payload) are in **bytes**.
    - ``bandwidth_bps`` is in **bits per second** (so LAN 1 Gbps = 1.0e9).
    - transfer time = ``bytes * 8 / bandwidth_bps``.

Jitter is ON by default with realistic per-profile values (wired links are
steady, wireless ones wobble). A seeded rng is therefore required whenever
any reachable link has jitter > 0 — the constructor fails fast if it is
missing. Set ``jitter_s: 0`` in a profile override to silence a link.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from src.config.factory import network_models
from src.models import Task
from src.network.base import NetworkModel

_BITS_PER_BYTE = 8.0

# Built-in profile defaults (bits/s, seconds). Override via YAML.
_BUILTIN_PROFILES: dict[str, dict[str, float]] = {
    "lan": {"bandwidth_bps": 1.0e9, "base_latency_s": 0.001, "jitter_s": 0.0002},
    "wifi": {"bandwidth_bps": 50.0e6, "base_latency_s": 0.010, "jitter_s": 0.005},
    "5g": {"bandwidth_bps": 100.0e6, "base_latency_s": 0.020, "jitter_s": 0.008},
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
    """One-way delay = base_latency + payload_bytes*8/bandwidth + jitter sample.

    Uplink (source -> executor) carries ``task.data_size``; downlink
    (executor -> source) carries ``task.result_size``. Directions resolve
    link specs independently, so asymmetric links work naturally.

    The ``expected_*`` methods return the deterministic part only (jitter
    has zero mean) and never touch the rng; the realized methods add one
    jitter sample per call.

    Args:
        default_profile: Profile name for links without an explicit override.
        profiles: Optional YAML overrides per profile name.
        links: Optional list of dicts with ``from``, ``to``, and either
            ``profile`` or explicit ``bandwidth_bps`` / ``base_latency_s``.
        rng: Seeded generator for jitter. Required if any reachable link
            (the default profile or an explicit link override) has
            jitter > 0 — which is the case for the built-in wireless
            profiles unless overridden.
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
        self._validate_rng_requirement()

    # ------------------------------------------------------------------
    # Realized delays (consume randomness — call once per transfer)
    # ------------------------------------------------------------------

    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        return self._realized_delay(source_id, target_id, task.data_size, t)

    def downlink_delay(
        self,
        executor_id: str,
        source_id: str,
        task: Task,
        t: float,
    ) -> float:
        return self._realized_delay(executor_id, source_id, task.result_size, t)

    # ------------------------------------------------------------------
    # Expected delays (deterministic — free to call any number of times)
    # ------------------------------------------------------------------

    def expected_uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        return self._expected_delay(source_id, target_id, task.data_size, t)

    def expected_downlink_delay(
        self,
        executor_id: str,
        source_id: str,
        task: Task,
        t: float,
    ) -> float:
        return self._expected_delay(executor_id, source_id, task.result_size, t)

    # ------------------------------------------------------------------
    # Shared internals
    # ------------------------------------------------------------------

    def _realized_delay(
        self, from_id: str, to_id: str, payload_bytes: float, t: float
    ) -> float:
        if from_id == to_id:
            return 0.0
        spec = self._resolve_spec(from_id, to_id)
        jitter = self._sample_jitter(spec.jitter_s)
        return max(0.0, self._deterministic_delay(spec, payload_bytes, from_id, to_id, t) + jitter)

    def _expected_delay(
        self, from_id: str, to_id: str, payload_bytes: float, t: float
    ) -> float:
        if from_id == to_id:
            return 0.0
        spec = self._resolve_spec(from_id, to_id)
        return self._deterministic_delay(spec, payload_bytes, from_id, to_id, t)

    def _deterministic_delay(
        self,
        spec: _LinkSpec,
        payload_bytes: float,
        from_id: str,
        to_id: str,
        t: float,
    ) -> float:
        """Latency + transfer for this payload. Subclasses may modulate by
        time or link identity (``from_id``/``to_id``/``t`` exist for them)."""
        transfer = 0.0 if spec.bandwidth_bps == float("inf") else (
            payload_bytes * _BITS_PER_BYTE / spec.bandwidth_bps
        )
        return spec.base_latency_s + transfer

    def _resolve_spec(self, from_id: str, to_id: str) -> _LinkSpec:
        key = (from_id, to_id)
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

    def _validate_rng_requirement(self) -> None:
        """Fail fast: jittery reachable links need a seeded rng."""
        if self._rng is not None:
            return
        reachable = [self._profile_specs[self.default_profile]]
        reachable.extend(self._pair_specs.values())
        jittery = [s for s in reachable if s.jitter_s > 0.0]
        if jittery:
            raise ValueError(
                "fluid_link has links with jitter > 0 (built-in wireless "
                "profiles default to jittery) but no rng was provided; "
                "pass a seeded numpy Generator or set jitter_s: 0 in the "
                "profile/link overrides"
            )

    def _sample_jitter(self, jitter_s: float) -> float:
        if jitter_s <= 0.0:
            return 0.0
        if self._rng is None:
            raise ValueError(
                "fluid_link network with jitter requires a seeded rng"
            )
        return float(self._rng.uniform(-jitter_s, jitter_s))
