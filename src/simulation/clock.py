"""Fixed-step simulation clock."""

from __future__ import annotations


class Clock:
    """Monotone clock advancing by `dt` seconds per tick, starting at t = 0.0."""

    __slots__ = ("dt", "t")

    def __init__(self, dt: float) -> None:
        if dt <= 0:
            raise ValueError(f"dt must be > 0, got {dt}")
        self.dt = float(dt)
        self.t = 0.0

    def advance(self) -> tuple[float, float]:
        """Advance one tick. Returns (t_start, t_end)."""
        t_start = self.t
        self.t = t_start + self.dt
        return t_start, self.t

    def reset(self) -> None:
        self.t = 0.0
