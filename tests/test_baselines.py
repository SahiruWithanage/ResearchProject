"""Tests for load_aware and latency_first baseline allocators."""

from __future__ import annotations

from src.config import parse_config
from src.config.factory import allocators
from src.controller.allocators import LatencyFirstAllocator, LoadAwareAllocator
from src.models import EdgeNode, NodeState, Task
from src.network.fluid_link import FluidLinkNetworkModel
from src.simulation import Environment
from src.simulation.estimates import CompletionEstimator
from tests.alloc_helpers import decision_context


def _node(node_id: str) -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type="source",
        cpu_capacity=4.0,
        memory_capacity=8.0,
        tier="edge",
    )


def _task(source: str = "node_1", data_size: float = 1e6) -> Task:
    return Task(
        task_id="t_001",
        arrival_time=0.0,
        task_type="compute",
        data_size=data_size,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id=source,
    )


def _state(node_id: str, queue: int, work: float | None = None) -> NodeState:
    # queued_work carries the backlog in work units; default to one unit per
    # queued task so a queue of N behaves like N unit-sized tasks.
    return NodeState(
        time_step=0.0,
        node_id=node_id,
        queue_length=queue,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
        queued_work=float(queue) if work is None else work,
    )


def test_baselines_registered() -> None:
    assert "load_aware" in allocators
    assert "latency_first" in allocators


def test_load_aware_picks_shortest_queue() -> None:
    a = LoadAwareAllocator()
    candidates = [_node("node_1"), _node("node_2")]
    states = {
        "node_1": _state("node_1", 5),
        "node_2": _state("node_2", 1),
    }
    ctx = decision_context(_task(), candidates, states)
    assert a.allocate(ctx) == "node_2"


def test_load_aware_measures_backlog_in_seconds_not_task_count() -> None:
    """One long job beats several short ones: load is work, not headcount."""
    a = LoadAwareAllocator()
    few_but_heavy = _node("few_but_heavy")
    many_but_light = _node("many_but_light")
    states = {
        # 1 task holding 40 work units vs 8 tasks holding 8 between them
        "few_but_heavy": _state("few_but_heavy", queue=1, work=40.0),
        "many_but_light": _state("many_but_light", queue=8, work=8.0),
    }
    ctx = decision_context(_task(), [few_but_heavy, many_but_light], states)
    assert a.allocate(ctx) == "many_but_light"


def test_load_aware_accounts_for_node_speed() -> None:
    """Equal backlogs, unequal machines: the faster node clears sooner."""
    a = LoadAwareAllocator()
    slow = EdgeNode(
        node_id="slow", node_type="helper", cpu_capacity=1.0,
        memory_capacity=8.0, tier="edge", cpu_speed=0.5,
    )
    fast = EdgeNode(
        node_id="fast", node_type="helper", cpu_capacity=4.0,
        memory_capacity=8.0, tier="edge", cpu_speed=2.0,
    )
    states = {
        "slow": _state("slow", queue=2, work=10.0),   # 10 / 0.5  = 20 s
        "fast": _state("fast", queue=6, work=10.0),   # 10 / 8.0  = 1.25 s
    }
    ctx = decision_context(_task(), [slow, fast], states)
    assert a.allocate(ctx) == "fast"


def test_latency_first_picks_lower_uplink_delay() -> None:
    from src.controller.context import DecisionContext

    import numpy as np

    net = FluidLinkNetworkModel(
        default_profile="wifi",
        links=[
            {"from": "node_1", "to": "node_2", "profile": "wifi"},
        ],
        rng=np.random.default_rng(7),
    )
    est = CompletionEstimator(net)
    a = LatencyFirstAllocator()
    candidates = [_node("node_1"), _node("node_2")]
    states = {n.node_id: _state(n.node_id, 0) for n in candidates}
    ctx = DecisionContext(
        task=_task(data_size=10e6),
        candidates=candidates,
        states=states,
        t=0.0,
        estimator=est,
    )
    assert a.allocate(ctx) == "node_1"


def test_remote_task_arrives_after_transfer() -> None:
    raw = {
        "seed": 1,
        "sim_duration": 5.0,
        "dt": 1.0,
        "network": {
            "type": "fluid_link",
            "default_profile": "wifi",
            "links": [
                {"from": "node_1", "to": "node_3", "profile": "lan"},
            ],
        },
        "controllers": [
            {
                "id": "ctrl",
                "allocator": {"type": "latency_first"},
                "manages": ["node_1", "node_3"],
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
                        "type": "fixed_interval",
                        "interval": 10.0,
                        "cpu_demand": 1.0,
                    }
                },
            },
            {
                "id": "node_3",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 1.0},
    }
    cfg = parse_config(raw)
    result = Environment(cfg).run()
    assert len(result.outcomes) == 1
    o = result.outcomes[0]
    assert o.transfer_end is not None
    assert o.compute_start is not None
    assert o.transfer_end >= o.decision_time
