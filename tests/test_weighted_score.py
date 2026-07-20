"""Tests for the weighted_score baseline allocator (Stage 5)."""

from __future__ import annotations

import numpy as np
import pytest

from src.config import parse_config
from src.config.factory import allocators
from src.controller.allocators import WeightedScoreAllocator
from src.controller.context import DecisionContext
from src.models import EdgeNode, NodeState, Task
from src.network.fluid_link import FluidLinkNetworkModel
from src.simulation import Environment
from src.simulation.estimates import CompletionEstimator


def _node(node_id: str, **overrides) -> EdgeNode:
    fields = {
        "node_id": node_id,
        "node_type": "helper",
        "cpu_capacity": 1.0,
        "memory_capacity": 8.0,
        "tier": "edge",
    }
    fields.update(overrides)
    return EdgeNode(**fields)


def _state(node_id: str, queue: int) -> NodeState:
    return NodeState(
        time_step=0.0,
        node_id=node_id,
        queue_length=queue,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
    )


def _task(data_size: float = 0.0, result_size: float = 0.0) -> Task:
    return Task(
        task_id="t1",
        arrival_time=0.0,
        task_type="compute",
        data_size=data_size,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="src",
        result_size=result_size,
    )


def _context(task, candidates, states, network=None) -> DecisionContext:
    net = network or FluidLinkNetworkModel(
        default_profile="wifi", profiles={"wifi": {"jitter_s": 0.0}}
    )
    return DecisionContext(
        task=task,
        candidates=candidates,
        states=states,
        t=0.0,
        estimator=CompletionEstimator(net),
    )


def test_registered() -> None:
    assert "weighted_score" in allocators


def test_weight_validation() -> None:
    with pytest.raises(ValueError, match="w_delay must be >= 0"):
        WeightedScoreAllocator(w_delay=-1.0)
    with pytest.raises(ValueError, match="at least one weight"):
        WeightedScoreAllocator(w_delay=0, w_load=0, w_compute=0, w_energy=0)


def test_default_weights_pick_earliest_expected_finish() -> None:
    # near_busy: no transfer but 5 tasks queued; far_free: 1.6 s transfer,
    # empty. Expected finish: near_busy 0+5+1=6 s, far_free 1.6+0+1=2.6 s.
    a = WeightedScoreAllocator()
    near_busy = _node("near_busy")
    far_free = _node("far_free")
    states = {"near_busy": _state("near_busy", 5), "far_free": _state("far_free", 0)}
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0}},
        links=[{"from": "src", "to": "near_busy",
                "bandwidth_bps": float("inf"), "base_latency_s": 0.0}],
    )
    ctx = _context(_task(data_size=10_000_000.0), [near_busy, far_free], states, net)
    assert a.allocate(ctx) == "far_free"


def test_delay_weight_flips_the_choice() -> None:
    # Same setup, but caring ONLY about network delay picks the near node.
    a = WeightedScoreAllocator(w_delay=1.0, w_load=0.0, w_compute=0.0)
    near_busy = _node("near_busy")
    far_free = _node("far_free")
    states = {"near_busy": _state("near_busy", 5), "far_free": _state("far_free", 0)}
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0}},
        links=[{"from": "src", "to": "near_busy",
                "bandwidth_bps": float("inf"), "base_latency_s": 0.0}],
    )
    ctx = _context(_task(data_size=10_000_000.0), [near_busy, far_free], states, net)
    assert a.allocate(ctx) == "near_busy"


def test_energy_weight_steers_away_from_expensive_node() -> None:
    cheap_slow = _node("cheap_slow", cpu_speed=0.5, energy_cost_factor=1.0)
    fast_costly = _node("fast_costly", cpu_speed=2.0, energy_cost_factor=20.0)
    states = {n.node_id: _state(n.node_id, 0) for n in (cheap_slow, fast_costly)}
    task = _task()  # no payloads: pure compute/energy trade-off

    # Ignoring energy: the fast node wins (0.5 s vs 2 s compute).
    speed_first = WeightedScoreAllocator(w_energy=0.0)
    ctx = _context(task, [cheap_slow, fast_costly], states)
    assert speed_first.allocate(ctx) == "fast_costly"

    # Caring about energy: fast node's 20x cost factor outweighs its speed
    # (energy: cheap 0.5*2.0*1=2.0 vs costly 0.5*0.5*20=5.0... scores:
    # cheap 2.0+0.5*2.0=?; with w_compute=1,w_energy=1:
    # cheap = 2.0 + 2.0 = 4.0, costly = 0.5 + 10.0 = 10.5).
    green = WeightedScoreAllocator(w_energy=1.0)
    assert green.allocate(ctx) == "cheap_slow"


def test_tie_breaks_on_node_id() -> None:
    a = WeightedScoreAllocator()
    n1, n2 = _node("alpha"), _node("beta")
    states = {"alpha": _state("alpha", 0), "beta": _state("beta", 0)}
    net = FluidLinkNetworkModel(default_profile="instant")
    ctx = _context(_task(), [n2, n1], states, net)
    assert a.allocate(ctx) == "alpha"


def test_end_to_end_run_with_weighted_score() -> None:
    raw = {
        "seed": 21,
        "sim_duration": 30.0,
        "dt": 0.01,
        "network": {"type": "fluid_link", "default_profile": "wifi"},
        "controllers": [
            {
                "id": "c",
                "allocator": {
                    "type": "weighted_score",
                    "w_delay": 1.0,
                    "w_load": 1.0,
                    "w_compute": 1.0,
                    "w_energy": 0.5,
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
                "source": {
                    "generator": {
                        "type": "poisson",
                        "rate": 1.2,
                        "cpu_demand": 2.0,
                        "data_size": 100000,
                        "deadline_offset": 20.0,
                    }
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "cpu_speed": 2.0,
                "energy_cost_factor": 3.0,
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 10.0},
    }
    result = Environment(parse_config(raw)).run()
    assert len(result.outcomes) > 10
    placed = {o.selected_node for o in result.outcomes if not o.task_lost}
    assert placed  # ran and allocated; overload pushes some work to the helper
