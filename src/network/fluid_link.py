"""Fluid link model: bandwidth + base latency + jitter, uplink and downlink.

Units (pinned; see resources/DELAY_MODEL.md):
    - ``Task.data_size`` (uplink payload) and ``Task.result_size``
      (downlink payload) are in **bytes**.
    - ``bandwidth_bps`` is in **bits per second** (so LAN 1 Gbps = 1.0e9).
    - transfer time = ``bytes * 8 / bandwidth_bps``.

Jitter is ON by default with realistic per-profile values (wired links are
steady, wireless ones wobble). A seeded rng is therefore required whenever
any reachable link has jitter > 0 - the constructor fails fast if it is
missing. Set ``jitter_s: 0`` in a profile override to silence a link.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.config.factory import network_models
from src.models import Task
from src.network.base import NetworkModel

_BITS_PER_BYTE = 8.0

_INF = float("inf")

# Built-in profile defaults (bits/s, seconds, kilometres). Override via YAML.
#
# Two distance properties belong to the *medium*, not the network as a whole:
#
#   max_range_km      how far it carries before the receiver cannot decode
#   full_rate_km      how close you must be to get the advertised rate;
#                     beyond this, path loss starts eating the bandwidth
#
# Radio degrades continuously and then drops out, so both are needed: with
# only a range limit a link would run at full speed and then die at a cliff,
# which is not how radio behaves.
#
# The wireless figures assume an **indoor industrial** setting - walls,
# metal and machinery - matching the base paper's factory context. Open
# outdoor line-of-sight would reach considerably further. These are
# literature-typical judgements, not measurements, and are meant to be
# swept as an experimental variable rather than treated as fact.
#
# Wired links are unlimited: a cable's length is an installation choice.
# (Copper Ethernet is limited to ~100 m per segment; the base paper
# specifies optical fibre between access points, which is what this models.)
#
# All of it is inert until nodes carry a `location` - without geometry every
# pair sits at distance zero, so nothing is ever out of range or degraded.
_BUILTIN_PROFILES: dict[str, dict[str, float]] = {
    "lan": {"bandwidth_bps": 1.0e9, "base_latency_s": 0.001, "jitter_s": 0.0002,
            "max_range_km": _INF, "full_rate_km": _INF},
    "wifi": {"bandwidth_bps": 50.0e6, "base_latency_s": 0.010, "jitter_s": 0.005,
             "max_range_km": 0.05, "full_rate_km": 0.01},
    "5g": {"bandwidth_bps": 100.0e6, "base_latency_s": 0.020, "jitter_s": 0.008,
           "max_range_km": 1.0, "full_rate_km": 0.1},
    "instant": {"bandwidth_bps": _INF, "base_latency_s": 0.0, "jitter_s": 0.0,
                "max_range_km": _INF, "full_rate_km": _INF},
    "custom": {"bandwidth_bps": 1.0e9, "base_latency_s": 0.001, "jitter_s": 0.0,
               "max_range_km": _INF, "full_rate_km": _INF},
}


@dataclass(frozen=True, slots=True)
class _LinkSpec:
    bandwidth_bps: float
    base_latency_s: float
    jitter_s: float
    max_range_km: float = _INF
    full_rate_km: float = _INF


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
            jitter > 0 - which is the case for the built-in wireless
            profiles unless overridden.
    """

    def __init__(
        self,
        *,
        default_profile: str = "wifi",
        profiles: dict[str, Any] | None = None,
        links: list[dict[str, Any]] | None = None,
        rng: np.random.Generator | None = None,
        jitter_entropy: int | None = None,
        positions: dict[str, tuple[float, float]] | None = None,
        propagation_speed_kms: float = 200_000.0,
        path_loss_exponent: float = 0.0,
        path_loss_reference_km: float = 0.1,
        min_bandwidth_fraction: float = 0.05,
    ) -> None:
        if propagation_speed_kms <= 0:
            raise ValueError(
                f"propagation_speed_kms must be > 0, got {propagation_speed_kms}"
            )
        if path_loss_exponent < 0:
            raise ValueError(
                f"path_loss_exponent must be >= 0, got {path_loss_exponent}"
            )
        if path_loss_reference_km <= 0:
            raise ValueError(
                f"path_loss_reference_km must be > 0, got {path_loss_reference_km}"
            )
        if not 0.0 < min_bandwidth_fraction <= 1.0:
            raise ValueError(
                f"min_bandwidth_fraction must be in (0, 1], "
                f"got {min_bandwidth_fraction}"
            )
        # Injected by the Environment from each node's `location`; empty
        # means no geometry, which reproduces the distance-free behaviour.
        # Entropy for keyed jitter. Drawn once at construction so it is a
        # stable property of the run, never of the transfer order.
        self._jitter_entropy = (
            int(jitter_entropy)
            if jitter_entropy is not None
            else (int(rng.integers(1 << 32)) if rng is not None else 0)
        )
        self._positions = dict(positions or {})
        self.propagation_speed_kms = float(propagation_speed_kms)
        self.path_loss_exponent = float(path_loss_exponent)
        self.path_loss_reference_km = float(path_loss_reference_km)
        self.min_bandwidth_fraction = float(min_bandwidth_fraction)
        self.default_profile = default_profile
        self._rng = rng
        self._pair_specs: dict[tuple[str, str], _LinkSpec] = {}
        # Directed pairs with no route at all (see can_reach).
        self._unreachable: set[tuple[str, str]] = set()
        self._profile_specs = self._build_profile_specs(profiles or {})
        for raw in links or []:
            self._register_link(raw)
        self._validate_rng_requirement()

    # ------------------------------------------------------------------
    # Realized delays (consume randomness - call once per transfer)
    # ------------------------------------------------------------------

    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        return self._realized_delay(
            source_id, target_id, task.data_size, t, task.task_id
        )

    def downlink_delay(
        self,
        executor_id: str,
        source_id: str,
        task: Task,
        t: float,
    ) -> float:
        return self._realized_delay(
            executor_id, source_id, task.result_size, t, task.task_id
        )

    # ------------------------------------------------------------------
    # Expected delays (deterministic - free to call any number of times)
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
        self, from_id: str, to_id: str, payload_bytes: float, t: float,
        key: str = "",
    ) -> float:
        if from_id == to_id:
            return 0.0
        spec = self._resolve_spec(from_id, to_id)
        jitter = self._sample_jitter(spec.jitter_s, from_id, to_id, key)
        return max(0.0, self._deterministic_delay(spec, payload_bytes, from_id, to_id, t) + jitter)

    def _expected_delay(
        self, from_id: str, to_id: str, payload_bytes: float, t: float
    ) -> float:
        if from_id == to_id:
            return 0.0
        spec = self._resolve_spec(from_id, to_id)
        return self._deterministic_delay(spec, payload_bytes, from_id, to_id, t)

    def expected_control_delay(
        self, source_id: str, target_id: str, payload_bytes: float
    ) -> float:
        """Control messages ride the same links as data, at t=0 conditions.

        Evaluated at t=0 rather than "now" so that a report's travel time
        stays a deterministic property of the topology. Time-varying models
        may override this if they want control traffic to feel the weather.
        """
        return self._expected_delay(source_id, target_id, payload_bytes, 0.0)

    def _deterministic_delay(
        self,
        spec: _LinkSpec,
        payload_bytes: float,
        from_id: str,
        to_id: str,
        t: float,
    ) -> float:
        """Latency + transfer for this payload, adjusted for distance.

        Subclasses may modulate by time or link identity (``from_id`` /
        ``to_id`` / ``t`` exist for them).
        """
        km = self._distance_km(from_id, to_id)
        bandwidth = self._bandwidth_at(spec, km)
        transfer = 0.0 if bandwidth == float("inf") else (
            payload_bytes * _BITS_PER_BYTE / bandwidth
        )
        return spec.base_latency_s + self._propagation_s(km) + transfer

    # ------------------------------------------------------------------
    # Distance (optional: only active when nodes carry a `location`)
    # ------------------------------------------------------------------

    def _distance_km(self, from_id: str, to_id: str) -> float:
        """Straight-line distance between two nodes, or 0 if unknown.

        Zero means "no geometry configured", which reproduces the previous
        distance-free behaviour exactly.
        """
        a = self._positions.get(from_id)
        b = self._positions.get(to_id)
        if a is None or b is None:
            return 0.0
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _propagation_s(self, km: float) -> float:
        """Time for the signal itself to cross the distance.

        Second-order at edge scale - roughly 5 microseconds per kilometre in
        fibre, against millisecond-scale latencies - but it is the honest
        floor on how fast anything can arrive, and it dominates once a
        distant cloud tier exists.
        """
        return km / self.propagation_speed_kms if km else 0.0

    def _bandwidth_at(self, spec: _LinkSpec, km: float) -> float:
        """Usable bandwidth after distance-dependent signal loss.

        Wireless signal strength falls off with distance, and the achievable
        rate falls with it. The base paper (Zhai et al.) models this through
        a path-loss term in the Shannon rate; we apply the same shape in
        reduced form: rate scales as ``(d / full_rate_km)^-exponent`` once a
        pair is further apart than the distance at which the medium still
        delivers its advertised rate.

        The onset distance is a property of the **medium**, so it lives on
        the profile (Wi-Fi starts fading within metres, a 5G cell holds up
        for hundreds). ``path_loss_reference_km`` remains as a fallback for
        profiles that do not declare one.

        The result is floored at ``min_bandwidth_fraction``: distance
        degrades a link, and only ``max_range_km`` or ``profile: none`` ends
        it, so a link never dies silently mid-transfer.

        Exponent 0 disables the effect entirely, which is the default.
        """
        onset = (
            spec.full_rate_km
            if spec.full_rate_km != float("inf")
            else self.path_loss_reference_km
        )
        if (
            self.path_loss_exponent <= 0.0
            or km <= onset
            or spec.bandwidth_bps == float("inf")
        ):
            return spec.bandwidth_bps
        ratio = km / onset
        factor = max(
            self.min_bandwidth_fraction, ratio ** (-self.path_loss_exponent)
        )
        return spec.bandwidth_bps * factor

    def _resolve_spec(self, from_id: str, to_id: str) -> _LinkSpec:
        key = (from_id, to_id)
        if key in self._pair_specs:
            return self._pair_specs[key]
        return self._profile_specs[self.default_profile]

    def can_reach(self, source_id: str, target_id: str) -> bool:
        """False when the pair was severed, or is simply too far apart.

        Out of range is a *reachability* fact, not a very slow link: a
        wireless signal that does not arrive cannot be waited out. It
        therefore joins the eligibility filter, so an out-of-range node is
        never chosen and a task with nowhere left to go is logged as lost.
        """
        if (source_id, target_id) in self._unreachable:
            return False
        if source_id == target_id:
            return True  # a node always reaches itself
        km = self._distance_km(source_id, target_id)
        if km <= 0.0:
            return True  # no geometry configured
        return km <= self._resolve_spec(source_id, target_id).max_range_km

    def range_km(self, source_id: str, target_id: str) -> float:
        """How far this pair's medium carries. Used by the UI."""
        return self._resolve_spec(source_id, target_id).max_range_km

    def distance_km(self, source_id: str, target_id: str) -> float:
        """Straight-line distance between two nodes, 0 if unknown."""
        return self._distance_km(source_id, target_id)

    def _register_link(self, raw: dict[str, Any]) -> None:
        from_id = str(raw["from"])
        to_id = str(raw["to"])
        # `profile: none` (or reachable: false) severs the pair in this
        # direction: no route, rather than a very slow one.
        if str(raw.get("profile", "")).lower() == "none" or raw.get(
            "reachable"
        ) is False:
            self._unreachable.add((from_id, to_id))
            self._pair_specs.pop((from_id, to_id), None)
            return
        self._unreachable.discard((from_id, to_id))
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
                max_range_km=float(base.get("max_range_km", _INF)),
                full_rate_km=float(base.get("full_rate_km", _INF)),
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

    def _sample_jitter(
        self, jitter_s: float, from_id: str, to_id: str, key: str
    ) -> float:
        """Jitter for one specific transfer, derived from its identity.

        Deliberately *not* the next value off a shared stream. Drawing
        sequentially would make a transfer's jitter depend on how many
        transfers happened before it - and that count is decided by the
        allocator, which is the thing under study. Two strategies would
        then face subtly different link weather, weakening the claim that
        they were compared in an identical world.

        Keying by ``(task, from, to)`` makes a given transfer's wobble a
        fixed property of the run: the same task crossing the same link
        gets the same value no matter what any allocator decided, and no
        matter in what order transfers occurred. Direction needs no
        separate tag, since ``from -> to`` already reverses on the way
        back.

        The same technique is used by ``varying_fluid_link`` for its
        per-window quality factors.
        """
        if jitter_s <= 0.0:
            return 0.0
        if self._rng is None:
            raise ValueError(
                "fluid_link network with jitter requires a seeded rng"
            )
        stamp = zlib.crc32(f"{key}|{from_id}->{to_id}".encode("utf-8"))
        rng = np.random.default_rng(
            np.random.SeedSequence([self._jitter_entropy, stamp])
        )
        return float(rng.uniform(-jitter_s, jitter_s))
