"""Trace-driven fluid link: per-link bandwidth replayed from measurements.

Same physics as ``fluid_link``, but chosen links follow a *measured*
bandwidth time series (e.g. the UCC Ireland 5G traces) instead of a
constant profile value:

.. code-block:: yaml

    network:
      type: trace_fluid_link
      default_profile: wifi
      params:
        traces:
          - from: node_1
            to: node_h
            file: resources/datasets/processed/5g_static_1.csv
            loop: true                 # repeat when the trace ends
            bandwidth_scale: 1.0
            min_bandwidth_bps: 10000   # floor: real traces dip to 0

The trace CSV needs columns ``t`` (seconds, ascending from 0) and
``bandwidth_bps``. The value holds until the next sample (step function).
Links without a trace behave exactly like ``fluid_link``. Base latency and
jitter still come from the resolved profile/link spec.

Delays remain a pure function of (link, t) — no randomness beyond the
usual jitter — so estimates stay deterministic and same-seed runs are
identical across allocators.
"""

from __future__ import annotations

import bisect
import csv
from typing import Any

from src.config.factory import network_models
from src.network.fluid_link import FluidLinkNetworkModel, _LinkSpec


class _BandwidthTrace:
    def __init__(
        self,
        *,
        file: str,
        loop: bool = False,
        bandwidth_scale: float = 1.0,
        time_scale: float = 1.0,
        min_bandwidth_bps: float = 10_000.0,
    ) -> None:
        if bandwidth_scale <= 0:
            raise ValueError(f"bandwidth_scale must be > 0, got {bandwidth_scale}")
        if time_scale <= 0:
            raise ValueError(f"time_scale must be > 0, got {time_scale}")
        if min_bandwidth_bps <= 0:
            raise ValueError(
                f"min_bandwidth_bps must be > 0, got {min_bandwidth_bps}"
            )
        times: list[float] = []
        bws: list[float] = []
        with open(file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                times.append(float(row["t"]) * time_scale)
                bws.append(
                    max(float(row["bandwidth_bps"]) * bandwidth_scale,
                        min_bandwidth_bps)
                )
        if not times:
            raise ValueError(f"bandwidth trace {file!r} has no rows")
        if times != sorted(times):
            raise ValueError(f"bandwidth trace {file!r} must be sorted by t")
        self._times = times
        self._bws = bws
        step = times[-1] - times[-2] if len(times) > 1 else 1.0
        self._duration = times[-1] + max(step, 1e-9)
        self.loop = bool(loop)

    def bandwidth(self, t: float) -> float:
        if self.loop:
            t = t % self._duration
        idx = bisect.bisect_right(self._times, t) - 1
        idx = max(0, min(idx, len(self._bws) - 1))
        return self._bws[idx]


@network_models.register("trace_fluid_link")
class TraceFluidLinkNetworkModel(FluidLinkNetworkModel):
    """Fluid link where selected links replay measured bandwidth over time."""

    def __init__(
        self,
        *,
        traces: list[dict[str, Any]] | None = None,
        **fluid_kwargs: Any,
    ) -> None:
        self._traces: dict[tuple[str, str], _BandwidthTrace] = {}
        for raw in traces or []:
            spec = dict(raw)
            from_id = str(spec.pop("from"))
            to_id = str(spec.pop("to"))
            self._traces[(from_id, to_id)] = _BandwidthTrace(**spec)
        super().__init__(**fluid_kwargs)

    def _deterministic_delay(
        self,
        spec: _LinkSpec,
        payload_bytes: float,
        from_id: str,
        to_id: str,
        t: float,
    ) -> float:
        trace = self._traces.get((from_id, to_id))
        if trace is None:
            return super()._deterministic_delay(
                spec, payload_bytes, from_id, to_id, t
            )
        bandwidth = trace.bandwidth(t)
        return spec.base_latency_s + payload_bytes * 8.0 / bandwidth
