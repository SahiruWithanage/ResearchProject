"""Time-varying fluid link: link quality drifts over time (seeded).

Same physics as ``fluid_link``, but each link's effective bandwidth (and
optionally base latency) is modulated by a per-interval factor: simulated
time is cut into windows of ``variation_period_s`` seconds, and every
(link, window) pair gets its own reproducible quality factor.

The factor is *counter-based*: derived by hashing (entropy, link, window
index) into a fresh seeded generator, never by drawing from a shared
stream. That means delays are a pure function of (link, t) — estimates
stay deterministic, nothing depends on call order, and same-seed runs are
identical regardless of which allocator is running.

This model doubles as the Stage 8 "network / latency variation" scenario
mechanism: a link can be genuinely good in one window and degraded in the
next, so an allocation that was right when it was made can become wrong.
"""

from __future__ import annotations

import math
import zlib
from typing import Any

import numpy as np

from src.config.factory import network_models
from src.network.fluid_link import FluidLinkNetworkModel, _LinkSpec


@network_models.register("varying_fluid_link")
class VaryingFluidLinkNetworkModel(FluidLinkNetworkModel):
    """Fluid link whose per-link quality changes every ``variation_period_s``.

    Extra args (on top of ``fluid_link``'s):
        variation_period_s: length of one quality window in simulated
            seconds. Default 10.0.
        bandwidth_variation: max relative bandwidth swing, e.g. 0.5 means
            each window's effective bandwidth is the profile value scaled
            by a factor in [0.5, 1.5]. Default 0.5.
        latency_variation: same idea for base latency. Default 0.0 (off).
        variation_entropy: integer mixed into the per-window hashing so
            different master seeds give different weather. Injected by the
            Environment from the master seed; defaults to 0 for direct use.
    """

    def __init__(
        self,
        *,
        variation_period_s: float = 10.0,
        bandwidth_variation: float = 0.5,
        latency_variation: float = 0.0,
        variation_entropy: int | None = None,
        **fluid_kwargs: Any,
    ) -> None:
        if variation_period_s <= 0:
            raise ValueError(
                f"variation_period_s must be > 0, got {variation_period_s}"
            )
        if not 0.0 <= bandwidth_variation < 1.0:
            raise ValueError(
                f"bandwidth_variation must be in [0, 1), got {bandwidth_variation}"
            )
        if not 0.0 <= latency_variation < 1.0:
            raise ValueError(
                f"latency_variation must be in [0, 1), got {latency_variation}"
            )
        self.variation_period_s = float(variation_period_s)
        self.bandwidth_variation = float(bandwidth_variation)
        self.latency_variation = float(latency_variation)
        self._variation_entropy = int(variation_entropy or 0)
        super().__init__(**fluid_kwargs)

    def _deterministic_delay(
        self,
        spec: _LinkSpec,
        payload_bytes: float,
        from_id: str,
        to_id: str,
        t: float,
    ) -> float:
        bw_factor, lat_factor = self._factors(from_id, to_id, t)
        bandwidth = spec.bandwidth_bps * bw_factor
        transfer = 0.0 if bandwidth == float("inf") else (
            payload_bytes * 8.0 / bandwidth
        )
        return spec.base_latency_s * lat_factor + transfer

    def _factors(self, from_id: str, to_id: str, t: float) -> tuple[float, float]:
        """Quality factors for this link in the window containing ``t``."""
        window = math.floor(t / self.variation_period_s)
        link_hash = zlib.crc32(f"{from_id}->{to_id}".encode("utf-8"))
        rng = np.random.default_rng(
            np.random.SeedSequence(
                [self._variation_entropy, link_hash, window & 0xFFFFFFFF]
            )
        )
        u, v = rng.uniform(-1.0, 1.0, size=2)
        bw_factor = 1.0 + self.bandwidth_variation * float(u)
        lat_factor = 1.0 + self.latency_variation * float(v)
        return bw_factor, lat_factor
