"""Validation against queueing theory: proves the timing model is accurate.

A single-worker node fed by Poisson arrivals with deterministic service
times is an M/D/1 queue, whose mean sojourn time has a closed-form answer
(Pollaczek-Khinchine): W = S + rho * S / (2 * (1 - rho)).

These tests run the full simulator at dt = 0.01 (the project's standard
experiment resolution, see DESIGN.md decision log) and check the measured
timings against the analytic values. They guard the whole pipeline:
generator -> controller -> enqueue -> worker drain -> completion stamping.
"""

from __future__ import annotations

import statistics
from typing import Any

import pytest

from src.config import parse_config
from src.simulation import Environment


def _single_worker_raw(
    *,
    seed: int,
    generator: dict[str, Any],
    sim_duration: float,
    dt: float = 0.01,
) -> dict[str, Any]:
    """One source node doing all its own work (offload threshold unreachable).

    The mandatory second node exists only to satisfy the >= 2 nodes rule;
    the huge max_local_queue keeps every task on node_1.
    """
    return {
        "seed": seed,
        "sim_duration": sim_duration,
        "dt": dt,
        "controllers": [
            {
                "id": "ctrl",
                "allocator": {
                    "type": "local_first_helper_offload",
                    "max_local_queue": 10**9,
                },
                "manages": ["node_1", "node_h"],
                "parent": None,
            }
        ],
        "nodes": [
            {
                "id": "node_1",
                "type": "source",
                "cpu_capacity": 1.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {"generator": generator},
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 1.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/validation", "log_state_every": 100.0},
    }


def test_unloaded_node_completes_in_exact_service_time() -> None:
    # Arrivals every 5 s, service 2 s: no queueing ever, so every task's
    # completion - decision must equal the service time exactly.
    raw = _single_worker_raw(
        seed=1,
        generator={
            "type": "fixed_interval",
            "interval": 5.0,
            "cpu_demand": 2.0,
            "deadline_offset": 100.0,
        },
        sim_duration=100.0,
    )
    result = Environment(parse_config(raw)).run()
    completed = [o for o in result.outcomes if o.actual_completion_time is not None]
    assert len(completed) >= 15
    for o in completed:
        sojourn = o.actual_completion_time - o.decision_time
        assert sojourn == pytest.approx(2.0, abs=1e-6)


def test_md1_mean_sojourn_matches_theory() -> None:
    # Poisson(0.5) arrivals, deterministic 1.0 s service -> rho = 0.5.
    # M/D/1: W = S + rho*S/(2*(1-rho)) = 1.0 + 0.5 = 1.5 s.
    raw = _single_worker_raw(
        seed=42,
        generator={
            "type": "poisson",
            "rate": 0.5,
            "cpu_demand": 1.0,
            "deadline_offset": 500.0,
        },
        sim_duration=2000.0,
    )
    result = Environment(parse_config(raw)).run()

    # Skip the warm-up transient and unfinished tasks; measure steady state.
    sojourns = [
        o.actual_completion_time - o.decision_time
        for o in result.outcomes
        if o.actual_completion_time is not None and o.decision_time > 200.0
    ]
    assert len(sojourns) > 500  # enough samples for a stable mean

    theory = 1.5
    assert statistics.mean(sojourns) == pytest.approx(theory, rel=0.10)

    # Nothing may leak to the helper, or the M/D/1 assumption is void.
    assert all(o.selected_node == "node_1" for o in result.outcomes)
