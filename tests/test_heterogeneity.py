"""Stage 4 heterogeneity: speed factors, memory admission, queue limits,
task suitability, node profiles, and task-loss behaviour."""

from __future__ import annotations

from typing import Any

import pytest

from src.config import parse_config
from src.config.loader import ConfigError
from src.controller import Controller
from src.controller.allocators import LoadAwareAllocator
from src.models import EdgeNode, Task
from src.simulation import Environment, NodeRuntime
from tests.alloc_helpers import instant_estimator


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


def _task(task_id: str = "t1", **overrides: Any) -> Task:
    fields: dict[str, Any] = {
        "task_id": task_id,
        "arrival_time": 0.0,
        "task_type": "compute",
        "data_size": 1.0,
        "cpu_demand": 1.0,
        "memory_demand": 1.0,
        "deadline": 100.0,
        "priority": 1,
        "source_node_id": "n1",
    }
    fields.update(overrides)
    return Task(**fields)


# ===========================================================================
# EdgeNode.is_suitable
# ===========================================================================

def test_suitable_by_default() -> None:
    assert _node().is_suitable(_task()) is True


def test_unsuitable_task_type() -> None:
    node = _node(accepts_task_types=("telemetry",))
    assert node.is_suitable(_task(task_type="video")) is False
    assert node.is_suitable(_task(task_type="telemetry")) is True


def test_unsuitable_memory_demand_exceeds_capacity() -> None:
    node = _node(memory_capacity=2.0)
    assert node.is_suitable(_task(memory_demand=3.0)) is False


def test_unsuitable_gpu_task_on_gpu_less_node() -> None:
    assert _node().is_suitable(_task(gpu_demand=1.0)) is False
    assert _node(gpu_capacity=4.0).is_suitable(_task(gpu_demand=1.0)) is True


# ===========================================================================
# NodeRuntime: cpu_speed
# ===========================================================================

def test_half_speed_node_takes_twice_as_long() -> None:
    rt = NodeRuntime(_node(cpu_speed=0.5))
    rt.enqueue(_task(cpu_demand=2.0))
    # 2.0 work units at 0.5 units/s -> 4.0 s; completes in tick [3, 4].
    for tick in range(3):
        assert rt.advance(1.0, float(tick)) == []
    completed = rt.advance(1.0, 3.0)
    assert len(completed) == 1
    assert completed[0][1] == pytest.approx(4.0)


def test_double_speed_node_halves_completion_time() -> None:
    rt = NodeRuntime(_node(cpu_speed=2.0))
    rt.enqueue(_task(cpu_demand=2.0))
    completed = rt.advance(1.0, 0.0)
    assert len(completed) == 1
    assert completed[0][1] == pytest.approx(1.0)


def test_default_speed_preserves_phase1_behaviour() -> None:
    rt = NodeRuntime(_node())
    rt.enqueue(_task(cpu_demand=2.0))
    assert rt.advance(1.0, 0.0) == []
    completed = rt.advance(1.0, 1.0)
    assert completed[0][1] == pytest.approx(2.0)


# ===========================================================================
# NodeRuntime: memory admission
# ===========================================================================

def test_memory_gate_delays_activation() -> None:
    # Two workers, but only memory for one 1.5-demand task at a time.
    rt = NodeRuntime(_node(cpu_capacity=2.0, memory_capacity=2.0))
    rt.enqueue(_task("t1", memory_demand=1.5, cpu_demand=1.0))
    rt.enqueue(_task("t2", memory_demand=1.5, cpu_demand=1.0))
    assert rt.active_count == 1
    assert rt.queue_length == 2

    completed = rt.advance(1.0, 0.0)  # t1 finishes, frees memory
    assert [pair[0].task_id for pair in completed] == ["t1"]
    assert rt.active_count == 1  # t2 promoted after memory freed


def test_memory_gate_keeps_fifo_order() -> None:
    # Head task too big right now; a small later task must NOT jump the line.
    rt = NodeRuntime(_node(cpu_capacity=2.0, memory_capacity=2.0))
    rt.enqueue(_task("big1", memory_demand=2.0))
    rt.enqueue(_task("big2", memory_demand=2.0))
    rt.enqueue(_task("small", memory_demand=0.1))
    assert rt.active_count == 1  # only big1 fits


def test_memory_unconstrained_uses_all_workers() -> None:
    rt = NodeRuntime(_node(cpu_capacity=2.0, memory_capacity=8.0))
    rt.enqueue(_task("t1"))
    rt.enqueue(_task("t2"))
    assert rt.active_count == 2


# ===========================================================================
# NodeRuntime: queue limits / has_room
# ===========================================================================

def test_has_room_respects_queue_limit() -> None:
    rt = NodeRuntime(_node(queue_limit=2))
    assert rt.has_room() is True
    rt.enqueue(_task("t1"))
    rt.enqueue(_task("t2"))
    assert rt.has_room() is False


def test_no_queue_limit_always_has_room() -> None:
    rt = NodeRuntime(_node())
    for i in range(50):
        rt.enqueue(_task(f"t{i}"))
    assert rt.has_room() is True


# ===========================================================================
# Controller: eligibility filtering and task loss
# ===========================================================================

def _controller(*runtimes: NodeRuntime) -> Controller:
    return Controller(
        id="ctrl",
        allocator=LoadAwareAllocator(),
        allocator_type="load_aware",
        managed_nodes=list(runtimes),
    )


def test_full_node_is_not_allocated() -> None:
    src = NodeRuntime(_node("n1", queue_limit=1))
    helper = NodeRuntime(_node("n2", node_type="helper"))
    ctrl = _controller(src, helper)
    src.enqueue(_task("occupier"))  # n1 now full

    outcome = ctrl.submit(_task("t1"), t=0.0, estimator=instant_estimator())
    assert outcome.selected_node == "n2"
    assert outcome.task_lost is False


def test_unsuitable_node_is_not_allocated() -> None:
    src = NodeRuntime(_node("n1", accepts_task_types=("telemetry",)))
    helper = NodeRuntime(_node("n2", node_type="helper"))
    ctrl = _controller(src, helper)

    outcome = ctrl.submit(
        _task("t1", task_type="video"), t=0.0, estimator=instant_estimator()
    )
    assert outcome.selected_node == "n2"


def test_task_lost_when_no_node_eligible() -> None:
    src = NodeRuntime(_node("n1", queue_limit=1))
    helper = NodeRuntime(_node("n2", node_type="helper", queue_limit=1))
    ctrl = _controller(src, helper)
    src.enqueue(_task("x1"))
    helper.enqueue(_task("x2"))

    outcome = ctrl.submit(_task("t1"), t=0.0, estimator=instant_estimator())
    assert outcome.task_lost is True
    assert outcome.selected_node is None
    assert outcome.estimated_completion_time is None


def test_task_survives_while_source_has_room() -> None:
    # The user's policy: never dropped while home has space. Helper full,
    # source below limit -> task stays at the source.
    src = NodeRuntime(_node("n1", queue_limit=5))
    helper = NodeRuntime(_node("n2", node_type="helper", queue_limit=1))
    ctrl = _controller(src, helper)
    helper.enqueue(_task("x1"))

    outcome = ctrl.submit(_task("t1"), t=0.0, estimator=instant_estimator())
    assert outcome.task_lost is False
    assert outcome.selected_node == "n1"


# ===========================================================================
# Config: node_profiles
# ===========================================================================

def _profile_raw() -> dict[str, Any]:
    return {
        "seed": 1,
        "sim_duration": 10.0,
        "dt": 1.0,
        "node_profiles": {
            "sensor_class": {
                "cpu_capacity": 1.0,
                "cpu_speed": 0.5,
                "memory_capacity": 2.0,
                "queue_limit": 5,
                "accepts_task_types": ["telemetry"],
            },
        },
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
                "profile": "sensor_class",
                "tier": "edge",
                "source": {
                    "generator": {"type": "poisson", "rate": 0.1, "task_type": "telemetry"}
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 1.0},
    }


def test_profile_fields_applied_to_node() -> None:
    cfg = parse_config(_profile_raw())
    node = cfg.nodes[0]
    assert node.cpu_speed == 0.5
    assert node.queue_limit == 5
    assert node.accepts_task_types == ("telemetry",)
    assert node.memory_capacity == 2.0


def test_node_fields_override_profile() -> None:
    raw = _profile_raw()
    raw["nodes"][0]["cpu_speed"] = 0.9
    cfg = parse_config(raw)
    assert cfg.nodes[0].cpu_speed == 0.9
    assert cfg.nodes[0].queue_limit == 5  # untouched profile value survives


def test_unknown_profile_rejected() -> None:
    raw = _profile_raw()
    raw["nodes"][0]["profile"] = "does_not_exist"
    with pytest.raises(ConfigError, match="not defined in node_profiles"):
        parse_config(raw)


def test_profile_with_unknown_field_rejected() -> None:
    raw = _profile_raw()
    raw["node_profiles"]["sensor_class"]["hovercraft"] = 3
    with pytest.raises(ConfigError, match="unknown fields"):
        parse_config(raw)


def test_invalid_cpu_speed_rejected() -> None:
    raw = _profile_raw()
    raw["nodes"][1]["cpu_speed"] = 0
    with pytest.raises(ConfigError, match="cpu_speed must be a number > 0"):
        parse_config(raw)


def test_invalid_queue_limit_rejected() -> None:
    raw = _profile_raw()
    raw["nodes"][1]["queue_limit"] = 0
    with pytest.raises(ConfigError, match="queue_limit must be >= 1"):
        parse_config(raw)


# ===========================================================================
# End to end: heterogeneity in a real run
# ===========================================================================

def test_lost_tasks_appear_in_environment_run() -> None:
    # Source can hold 1 task; helper only accepts a type we never emit.
    # Long tasks + fast arrivals guarantee overflow -> losses.
    raw = {
        "seed": 7,
        "sim_duration": 10.0,
        "dt": 1.0,
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
                "queue_limit": 1,
                "source": {
                    "generator": {
                        "type": "fixed_interval",
                        "interval": 1.0,
                        "cpu_demand": 100.0,
                        "deadline_offset": 500.0,
                    }
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "accepts_task_types": ["never_emitted"],
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 1.0},
    }
    result = Environment(parse_config(raw)).run()
    lost = [o for o in result.outcomes if o.task_lost]
    placed = [o for o in result.outcomes if not o.task_lost]
    assert len(placed) == 1  # only the first task fits at the source
    assert len(lost) == len(result.outcomes) - 1
    assert all(o.selected_node is None for o in lost)
    assert all(o.actual_completion_time is None for o in lost)


def test_heterogeneous_speeds_shift_completion_times() -> None:
    # Same workload on a slow vs fast node: the fast node must finish sooner.
    def run(speed: float) -> float:
        raw = {
            "seed": 3,
            "sim_duration": 50.0,
            "dt": 0.01,
            "controllers": [
                {
                    "id": "c",
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
                    "cpu_speed": speed,
                    "source": {
                        "generator": {
                            "type": "fixed_interval",
                            "interval": 10.0,
                            "cpu_demand": 2.0,
                            "deadline_offset": 100.0,
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
        result = Environment(parse_config(raw)).run()
        completed = [
            o for o in result.outcomes if o.actual_completion_time is not None
        ]
        assert completed
        first = completed[0]
        return first.actual_completion_time - first.decision_time

    slow = run(0.5)
    fast = run(2.0)
    assert slow == pytest.approx(4.0, abs=0.05)   # 2.0 work / 0.5 speed
    assert fast == pytest.approx(1.0, abs=0.05)   # 2.0 work / 2.0 speed
