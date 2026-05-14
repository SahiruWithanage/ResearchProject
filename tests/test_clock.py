"""Tests for the fixed-step simulation clock."""

from __future__ import annotations

import pytest

from src.simulation import Clock


def test_clock_starts_at_zero() -> None:
    clock = Clock(dt=1.0)
    assert clock.t == 0.0


def test_clock_stores_dt() -> None:
    clock = Clock(dt=0.25)
    assert clock.dt == 0.25


def test_clock_dt_must_be_positive() -> None:
    with pytest.raises(ValueError, match="dt must be > 0"):
        Clock(dt=0)
    with pytest.raises(ValueError, match="dt must be > 0"):
        Clock(dt=-0.5)


def test_clock_advance_returns_window_and_updates_t() -> None:
    clock = Clock(dt=1.0)
    t_start, t_end = clock.advance()
    assert t_start == 0.0
    assert t_end == 1.0
    assert clock.t == 1.0


def test_clock_advances_repeatedly() -> None:
    clock = Clock(dt=0.5)
    windows = [clock.advance() for _ in range(4)]
    assert windows == [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]
    assert clock.t == 2.0


def test_clock_reset_returns_to_zero() -> None:
    clock = Clock(dt=1.0)
    clock.advance()
    clock.advance()
    clock.reset()
    assert clock.t == 0.0
