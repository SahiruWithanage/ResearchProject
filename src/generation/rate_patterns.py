"""Pluggable arrival-rate patterns: how a source's rate changes over time.

The Poisson generator's ``rate`` may be a plain number (constant, the
Phase 1 behaviour) or a pattern spec:

.. code-block:: yaml

    rate: 0.8                                            # constant
    rate: {pattern: sinusoidal, base: 0.5, amplitude: 0.4, period: 200}
    rate: {pattern: piecewise, segments: [{t_start: 0, rate: 0.3},
                                          {t_start: 100, rate: 2.0},
                                          {t_start: 130, rate: 0.3}]}

``sinusoidal`` gives smooth day/night-style load curves; ``piecewise``
gives calm/burst/calm workload fluctuation (the Stage 8 workload
scenario). New patterns register via ``@rate_patterns.register``.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from src.config.factory import rate_patterns


class RatePattern(ABC):
    """Contract: instantaneous ``rate(t)`` plus a finite ``max_rate``.

    ``max_rate`` bounds the thinning algorithm in the Poisson generator -
    ``rate(t) <= max_rate()`` must hold for all t.
    """

    is_constant: bool = False

    @abstractmethod
    def rate(self, t: float) -> float: ...

    @abstractmethod
    def max_rate(self) -> float: ...


@rate_patterns.register("constant")
class ConstantRate(RatePattern):
    is_constant = True

    def __init__(self, *, value: float) -> None:
        if value < 0:
            raise ValueError(f"rate must be >= 0, got {value}")
        self.value = float(value)

    def rate(self, t: float) -> float:
        return self.value

    def max_rate(self) -> float:
        return self.value


@rate_patterns.register("sinusoidal")
class SinusoidalRate(RatePattern):
    """``base + amplitude * sin(2*pi*(t + phase)/period)``, floored at 0."""

    def __init__(
        self,
        *,
        base: float,
        amplitude: float,
        period: float,
        phase: float = 0.0,
    ) -> None:
        if base < 0:
            raise ValueError(f"base must be >= 0, got {base}")
        if amplitude < 0:
            raise ValueError(f"amplitude must be >= 0, got {amplitude}")
        if period <= 0:
            raise ValueError(f"period must be > 0, got {period}")
        self.base = float(base)
        self.amplitude = float(amplitude)
        self.period = float(period)
        self.phase = float(phase)

    def rate(self, t: float) -> float:
        value = self.base + self.amplitude * math.sin(
            2.0 * math.pi * (t + self.phase) / self.period
        )
        return max(0.0, value)

    def max_rate(self) -> float:
        return self.base + self.amplitude


@rate_patterns.register("piecewise")
class PiecewiseRate(RatePattern):
    """Constant segments: the rate of the last segment with t_start <= t.

    Before the first segment's ``t_start`` the rate is 0.
    """

    def __init__(self, *, segments: list[dict[str, Any]]) -> None:
        if not isinstance(segments, list) or not segments:
            raise ValueError("piecewise needs a non-empty 'segments' list")
        parsed: list[tuple[float, float]] = []
        for i, seg in enumerate(segments):
            if not isinstance(seg, Mapping) or "t_start" not in seg or "rate" not in seg:
                raise ValueError(
                    f"segments[{i}] must be a mapping with 't_start' and 'rate'"
                )
            t_start = float(seg["t_start"])
            rate = float(seg["rate"])
            if rate < 0:
                raise ValueError(f"segments[{i}].rate must be >= 0, got {rate}")
            parsed.append((t_start, rate))
        starts = [s for s, _ in parsed]
        if starts != sorted(starts):
            raise ValueError("segments must be sorted by t_start")
        self.segments = parsed

    def rate(self, t: float) -> float:
        current = 0.0
        for t_start, rate in self.segments:
            if t >= t_start:
                current = rate
            else:
                break
        return current

    def max_rate(self) -> float:
        return max(rate for _, rate in self.segments)


@rate_patterns.register("trace")
class TraceRate(RatePattern):
    """Step-function arrival rate replayed from a measured trace.

    The CSV needs columns ``t`` (seconds, ascending from 0) and ``rate``
    (tasks per second). The rate holds until the next sample. After the
    last sample the rate is 0 unless ``loop`` is true, in which case the
    trace repeats. ``rate_scale`` / ``time_scale`` rescale intensity and
    playback speed (time_scale 2.0 = play twice as slow).
    """

    def __init__(
        self,
        *,
        file: str,
        rate_scale: float = 1.0,
        time_scale: float = 1.0,
        loop: bool = False,
    ) -> None:
        if rate_scale < 0:
            raise ValueError(f"rate_scale must be >= 0, got {rate_scale}")
        if time_scale <= 0:
            raise ValueError(f"time_scale must be > 0, got {time_scale}")
        import csv

        times: list[float] = []
        rates: list[float] = []
        with open(file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                times.append(float(row["t"]) * time_scale)
                rates.append(float(row["rate"]) * rate_scale)
        if not times:
            raise ValueError(f"trace rate file {file!r} has no rows")
        if times != sorted(times):
            raise ValueError(f"trace rate file {file!r} must be sorted by t")
        if any(r < 0 for r in rates):
            raise ValueError(f"trace rate file {file!r} has negative rates")
        self._times = times
        self._rates = rates
        # One sample-interval past the last sample marks the trace's end.
        step = times[-1] - times[-2] if len(times) > 1 else 1.0
        self._duration = times[-1] + max(step, 1e-9)
        self.loop = bool(loop)

    def rate(self, t: float) -> float:
        import bisect

        if self.loop:
            t = t % self._duration
        elif t >= self._duration or t < self._times[0]:
            return 0.0
        idx = bisect.bisect_right(self._times, t) - 1
        if idx < 0:
            return 0.0
        return self._rates[idx]

    def max_rate(self) -> float:
        return max(self._rates)


def build_rate_pattern(spec: Any, where: str = "rate") -> RatePattern:
    """Number -> ConstantRate; mapping with 'pattern' -> registered class."""
    if isinstance(spec, bool):
        raise ValueError(f"{where}: booleans are not valid rates")
    if isinstance(spec, (int, float)):
        return ConstantRate(value=float(spec))
    if isinstance(spec, Mapping):
        params = dict(spec)
        name = params.pop("pattern", None)
        if not isinstance(name, str):
            raise ValueError(
                f"{where}: rate spec needs a 'pattern' name, got {spec!r}"
            )
        cls = rate_patterns.get(name)
        try:
            return cls(**params)
        except TypeError as e:
            raise ValueError(f"{where}: bad params for pattern {name!r}: {e}") from e
    raise ValueError(
        f"{where}: expected a number or a rate-pattern mapping, got "
        f"{type(spec).__name__}: {spec!r}"
    )
