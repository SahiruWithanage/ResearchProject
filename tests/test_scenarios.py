"""Stage 8 instability: node failure/recovery, reliability decay, thresholds."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.config import parse_config
from src.config.factory import allocators, scenarios
from src.config.loader import ConfigError
from src.controller import HeartbeatObservability
from src.controller.allocators import ReliabilityThresholdAllocator
from src.models import EdgeNode, NodeState, Task
from src.simulation import Environment, NodeRuntime
from tests.alloc_helpers import decision_context


def _node(node_id: str = "n1", **overrides: Any) -> EdgeNode:
    fields: dict[str, Any] = {
        "node_id": node_id,
        "node_type": "source",
        "cpu_capacity": 1.0,
        "memory_capacity": 8.0,
        "tier": "edge",
    }
    fields.update(overrides)
    return EdgeNode(**fields)


def _task(task_id: str = "t1") -> Task:
    return Task(
        task_id=task_id,
        arrival_time=0.0,
        task_type="compute",
        data_size=1.0,
        cpu_demand=2.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="n1",
    )


def _state(node_id: str, queue: int = 0, reliability: float = 1.0) -> NodeState:
    # one work unit per queued task, so backlog ordering matches queue order
    return NodeState(
        time_step=0.0,
        node_id=node_id,
        queue_length=queue,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
        reliability_score=reliability,
        queued_work=float(queue),
    )


# ===========================================================================
# NodeRuntime failure lifecycle
# ===========================================================================

def test_fail_evicts_all_tasks_and_stops_work() -> None:
    rt = NodeRuntime(_node())
    rt.enqueue(_task("t1"))
    rt.enqueue(_task("t2"))
    evicted = rt.fail()
    assert sorted(t.task_id for t in evicted) == ["t1", "t2"]
    assert rt.queue_length == 0
    assert rt.is_available() is False
    assert rt.advance(1.0, 0.0) == []  # crashed nodes do nothing


def test_recovery_speed_factor_slows_work() -> None:
    rt = NodeRuntime(_node())
    rt.fail()
    rt.begin_recovery(speed_factor=0.5)
    assert rt.failure_state == "recovering"
    rt.enqueue(_task("t1"))  # cpu_demand 2.0 at 0.5x speed -> 4 s
    completed = []
    for tick in range(4):
        completed += rt.advance(1.0, float(tick))
    assert len(completed) == 1
    assert completed[0][1] == pytest.approx(4.0)
    rt.restore()
    assert rt.failure_state == "normal"
    assert rt.effective_speed == 1.0


def test_snapshot_carries_failure_and_reliability() -> None:
    rt = NodeRuntime(_node())
    rt.reliability_score = 0.42
    rt.fail()
    snap = rt.snapshot(1.0)
    assert snap.failure_state == "failed"
    assert snap.reliability_score == 0.42


# ===========================================================================
# Scenario plug-ins
# ===========================================================================

def test_scenarios_registered() -> None:
    assert "node_failure" in scenarios
    assert "reliability_decay" in scenarios


def test_scenario_validation() -> None:
    from src.simulation.scenarios import (
        NodeFailureScenario,
        ReliabilityDecayScenario,
    )

    with pytest.raises(ValueError, match="recover_at must be > fail_at"):
        NodeFailureScenario(node="n", fail_at=10.0, recover_at=5.0)
    with pytest.raises(ValueError, match="to_value must be in"):
        ReliabilityDecayScenario(node="n", start=0, end=10, to_value=1.5)


def test_config_rejects_unknown_scenario_node() -> None:
    raw = _instability_raw()
    raw["scenarios"][0]["node"] = "ghost"
    with pytest.raises(ConfigError, match="unknown node 'ghost'"):
        parse_config(raw)


# ===========================================================================
# reliability_threshold allocator
# ===========================================================================

def test_reliability_threshold_registered() -> None:
    assert "reliability_threshold" in allocators


def test_threshold_avoids_unreliable_node() -> None:
    a = ReliabilityThresholdAllocator(min_reliability=0.5)
    good, shaky = _node("good"), _node("shaky")
    states = {
        # shaky is empty (attractive) but below threshold.
        "good": _state("good", queue=3, reliability=0.9),
        "shaky": _state("shaky", queue=0, reliability=0.2),
    }
    ctx = decision_context(_task(), [good, shaky], states)
    assert a.allocate(ctx) == "good"


def test_threshold_falls_back_when_all_unreliable() -> None:
    a = ReliabilityThresholdAllocator(min_reliability=0.9)
    n1, n2 = _node("na"), _node("nb")
    states = {
        "na": _state("na", queue=2, reliability=0.5),
        "nb": _state("nb", queue=1, reliability=0.4),
    }
    ctx = decision_context(_task(), [n1, n2], states)
    assert a.allocate(ctx) == "nb"  # least queue among the fallback pool


# ===========================================================================
# Heartbeat silence during failure
# ===========================================================================

def test_failed_node_goes_silent_on_heartbeat() -> None:
    rt = NodeRuntime(_node())
    obs = HeartbeatObservability(interval=1.0)
    obs.attach([rt])

    rt.enqueue(_task("t1"))
    obs.refresh(1.0)
    assert obs.observe(1.0)["n1"].queue_length == 1  # report arrived

    rt.fail()
    obs.refresh(2.0)
    obs.refresh(3.0)
    # No reports while dead: belief frozen at the pre-failure snapshot.
    believed = obs.observe(3.0)["n1"]
    assert believed.queue_length == 1
    assert believed.failure_state == "normal"

    rt.restore()
    rt.enqueue(_task("t2"))
    obs.refresh(4.0)
    assert obs.observe(4.0)["n1"].queue_length == 1  # fresh report resumes


# ===========================================================================
# End to end
# ===========================================================================

def _instability_raw() -> dict[str, Any]:
    return {
        "seed": 13,
        "sim_duration": 30.0,
        "dt": 0.01,
        "scenarios": [
            {
                "type": "node_failure",
                "node": "node_h",
                "fail_at": 10.0,
                "recover_at": 20.0,
                "recovery_duration": 5.0,
                "recovery_speed_factor": 0.5,
            },
        ],
        "controllers": [
            {
                "id": "c",
                "allocator": {"type": "load_aware"},
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
                "queue_limit": 3,
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
                "cpu_capacity": 2.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 1.0},
    }


def test_failure_episode_loses_and_redirects_tasks() -> None:
    result = Environment(parse_config(_instability_raw())).run()
    lost = [o for o in result.outcomes if o.task_lost]
    assert lost  # eviction and/or overflow during the outage

    # Nothing is *placed on* the helper while it is down.
    placed_during_outage = [
        o
        for o in result.outcomes
        if o.selected_node == "node_h" and 10.0 <= o.decision_time < 20.0
    ]
    assert placed_during_outage == []

    # The helper is used again after recovery.
    placed_after = [
        o
        for o in result.outcomes
        if o.selected_node == "node_h" and o.decision_time >= 20.0
    ]
    assert placed_after

    # The state log sees the failure and the recovery ramp.
    states = {
        (s.time_step, s.node_id): s.failure_state for s in result.snapshots
    }
    assert states[(15.0, "node_h")] == "failed"
    assert states[(22.0, "node_h")] == "recovering"
    assert states[(28.0, "node_h")] == "normal"


def test_reliability_decay_visible_in_snapshots() -> None:
    raw = _instability_raw()
    raw["scenarios"] = [
        {
            "type": "reliability_decay",
            "node": "node_h",
            "start": 0.0,
            "end": 20.0,
            "to_value": 0.2,
        }
    ]
    result = Environment(parse_config(raw)).run()
    scores = {
        (s.time_step, s.node_id): s.reliability_score for s in result.snapshots
    }
    assert scores[(0.0, "node_h")] == pytest.approx(1.0)
    assert scores[(10.0, "node_h")] == pytest.approx(0.6, abs=0.01)
    assert scores[(25.0, "node_h")] == pytest.approx(0.2)


def test_instability_run_is_deterministic() -> None:
    a = Environment(parse_config(copy.deepcopy(_instability_raw()))).run()
    b = Environment(parse_config(copy.deepcopy(_instability_raw()))).run()
    assert [(o.task_id, o.selected_node, o.task_lost) for o in a.outcomes] == [
        (o.task_id, o.selected_node, o.task_lost) for o in b.outcomes
    ]
