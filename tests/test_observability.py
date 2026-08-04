"""Control-plane realism: heartbeat staleness and scheduling delay."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.config import parse_config
from src.config.factory import observability_models
from src.config.loader import ConfigError
from src.controller import HeartbeatObservability, PerfectObservability
from src.models import EdgeNode, Task
from src.simulation import Environment, NodeRuntime


def _node(node_id: str = "n1") -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type="source",
        cpu_capacity=2.0,
        memory_capacity=8.0,
        tier="edge",
    )


def _task(task_id: str) -> Task:
    return Task(
        task_id=task_id,
        arrival_time=0.0,
        task_type="compute",
        data_size=1.0,
        cpu_demand=5.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="n1",
    )


# ===========================================================================
# Models
# ===========================================================================

def test_builtin_observability_models_registered() -> None:
    assert "perfect" in observability_models
    assert "heartbeat" in observability_models


def test_perfect_observability_sees_live_truth() -> None:
    rt = NodeRuntime(_node())
    obs = PerfectObservability()
    obs.attach([rt])
    assert obs.observe(0.0)["n1"].queue_length == 0
    rt.enqueue(_task("t1"))
    assert obs.observe(0.0)["n1"].queue_length == 1  # instantly visible


def test_heartbeat_observability_lags_by_interval() -> None:
    rt = NodeRuntime(_node())
    obs = HeartbeatObservability(interval=1.0)
    obs.attach([rt])

    rt.enqueue(_task("t1"))
    rt.enqueue(_task("t2"))
    obs.refresh(0.5)  # next report is at t=1.0: nothing new yet
    assert obs.observe(0.5)["n1"].queue_length == 0  # still the t=0 view

    obs.refresh(1.0)  # heartbeat fires
    assert obs.observe(1.0)["n1"].queue_length == 2


def test_heartbeat_report_delay_defers_visibility() -> None:
    rt = NodeRuntime(_node())
    obs = HeartbeatObservability(interval=1.0, report_delay=0.5)
    obs.attach([rt])

    rt.enqueue(_task("t1"))
    obs.refresh(1.0)  # captured at 1.0, arrives at 1.5
    assert obs.observe(1.0)["n1"].queue_length == 0
    obs.refresh(1.5)
    assert obs.observe(1.5)["n1"].queue_length == 1


def test_heartbeat_validation() -> None:
    with pytest.raises(ValueError, match="interval must be > 0"):
        HeartbeatObservability(interval=0.0)
    with pytest.raises(ValueError, match="report_delay must be >= 0"):
        HeartbeatObservability(interval=1.0, report_delay=-1.0)


# ===========================================================================
# Config plumbing
# ===========================================================================

def _raw(extra_ctrl: dict[str, Any] | None = None) -> dict[str, Any]:
    ctrl: dict[str, Any] = {
        "id": "c",
        "allocator": {"type": "load_aware"},
        "manages": ["node_1", "node_h"],
        "parent": None,
    }
    if extra_ctrl:
        ctrl.update(extra_ctrl)
    return {
        "seed": 8,
        "sim_duration": 30.0,
        "dt": 0.01,
        "controllers": [ctrl],
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
                        "interval": 0.5,
                        "cpu_demand": 2.0,
                        "deadline_offset": 60.0,
                    }
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 1.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 10.0},
    }


def test_config_defaults_to_perfect_and_zero_delay() -> None:
    cfg = parse_config(_raw())
    assert cfg.controllers[0].observability.type == "perfect"
    assert cfg.controllers[0].scheduling_delay == 0.0


def test_config_parses_heartbeat_block() -> None:
    cfg = parse_config(
        _raw({"observability": {"type": "heartbeat", "interval": 2.0,
                                "report_delay": 0.1}})
    )
    obs = cfg.controllers[0].observability
    assert obs.type == "heartbeat"
    assert obs.params == {"interval": 2.0, "report_delay": 0.1}


def test_config_rejects_negative_scheduling_delay() -> None:
    with pytest.raises(ConfigError, match="scheduling_delay"):
        parse_config(_raw({"scheduling_delay": -0.5}))


def test_unknown_observability_type_raises_at_build() -> None:
    cfg = parse_config(_raw({"observability": {"type": "psychic"}}))
    with pytest.raises(KeyError, match="unknown observability model"):
        Environment(cfg)


# ===========================================================================
# End to end: staleness changes decisions; delay shifts timings
# ===========================================================================

def test_stale_view_changes_allocation_behaviour() -> None:
    """Staleness only bites when node state moves unpredictably.

    The controller remembers what it dispatched, so with a perfectly
    regular workload it can dead-reckon through any amount of staleness
    and reach the same decisions as a live view. What it cannot predict is
    *when work finishes*, so this uses variable task sizes: the belief then
    drifts from reality between reports, and the decisions diverge. That
    unpredictable component is the uncertainty the Bayesian layer targets.
    """
    varied = {"cpu_demand": {"dist": "uniform", "low": 0.5, "high": 6.0}}

    def spec(extra_ctrl=None):
        raw = _raw(extra_ctrl)
        raw["nodes"][0]["source"]["generator"].update(varied)
        return raw

    fresh = Environment(parse_config(spec())).run()
    stale = Environment(
        parse_config(spec({"observability": {"type": "heartbeat",
                                             "interval": 10.0}}))
    ).run()
    placements_fresh = [
        o.selected_node
        for o in sorted(fresh.outcomes, key=lambda o: o.task_id)
    ]
    placements_stale = [
        o.selected_node
        for o in sorted(stale.outcomes, key=lambda o: o.task_id)
    ]
    assert placements_fresh != placements_stale


def test_stale_run_is_deterministic() -> None:
    spec = _raw({"observability": {"type": "heartbeat", "interval": 10.0}})
    a = Environment(parse_config(copy.deepcopy(spec))).run()
    b = Environment(parse_config(copy.deepcopy(spec))).run()
    assert [o.selected_node for o in a.outcomes] == [
        o.selected_node for o in b.outcomes
    ]
    assert [o.actual_completion_time for o in a.outcomes] == [
        o.actual_completion_time for o in b.outcomes
    ]


def test_scheduling_delay_shifts_dispatch_and_eta() -> None:
    no_delay = Environment(parse_config(_raw())).run()
    delayed = Environment(
        parse_config(_raw({"scheduling_delay": 0.5}))
    ).run()

    o0 = min(no_delay.outcomes, key=lambda o: o.task_id)
    o1 = min(delayed.outcomes, key=lambda o: o.task_id)
    assert o0.transfer_start == o0.decision_time
    assert o1.transfer_start == pytest.approx(o1.decision_time + 0.5)
    # Work genuinely starts later, so completion shifts too.
    assert o1.compute_start >= o1.decision_time + 0.5
    assert o1.actual_completion_time > o0.actual_completion_time


def test_scheduling_delay_applies_to_local_tasks_via_transit() -> None:
    spec = _raw({"scheduling_delay": 0.25})
    result = Environment(parse_config(spec)).run()
    local = [o for o in result.outcomes if o.selected_node == "node_1"]
    assert local
    for o in local:
        if o.compute_start is not None:
            assert o.compute_start == pytest.approx(o.decision_time + 0.25)
