"""Tests for the Controller: submit, record_completion, allocator integration."""

from __future__ import annotations

import pytest

from src.controller import Allocator, Controller
from src.controller.allocators import LocalFirstHelperOffloadAllocator
from src.controller.context import DecisionContext
from src.models import EdgeNode, Task
from src.simulation import NodeRuntime
from tests.alloc_helpers import instant_estimator


# ---------------------------------------------------------------------------
# Helpers and fakes
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str = "source") -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type=node_type,
        cpu_capacity=4.0,
        memory_capacity=8.0,
        tier="edge",
    )


def _task(
    task_id: str,
    *,
    source: str | None = "node_1",
    data_size: float = 1.0,
    cpu_demand: float = 1.0,
    deadline: float = 100.0,
) -> Task:
    return Task(
        task_id=task_id,
        arrival_time=0.0,
        task_type="compute",
        data_size=data_size,
        cpu_demand=cpu_demand,
        memory_demand=1.0,
        deadline=deadline,
        priority=1,
        source_node_id=source,
    )


class _AlwaysFirstCandidateAllocator(Allocator):
    def allocate(self, context: DecisionContext) -> str:
        return context.candidates[0].node_id


class _AlwaysGhostAllocator(Allocator):
    """Always returns a node_id not in the candidate set (used for the rejection test)."""

    def allocate(self, context: DecisionContext) -> str:
        return "ghost_node"


def _make_controller(
    allocator: Allocator | None = None,
    allocator_type: str = "test_allocator",
) -> Controller:
    runtimes = [
        NodeRuntime(_node("node_1", "source")),
        NodeRuntime(_node("node_2", "helper")),
    ]
    return Controller(
        id="ctrl_test",
        allocator=allocator or _AlwaysFirstCandidateAllocator(),
        allocator_type=allocator_type,
        managed_nodes=runtimes,
    )


# ===========================================================================
# Construction
# ===========================================================================


def test_controller_construction_requires_managed_nodes() -> None:
    with pytest.raises(ValueError, match="no managed nodes"):
        Controller(
            id="ctrl_empty",
            allocator=_AlwaysFirstCandidateAllocator(),
            allocator_type="test",
            managed_nodes=[],
        )


def test_controller_managed_node_ids_returns_insertion_order() -> None:
    ctrl = _make_controller()
    assert ctrl.managed_node_ids == ["node_1", "node_2"]


def test_controller_default_parent_is_none() -> None:
    ctrl = _make_controller()
    assert ctrl.parent_id is None


def test_controller_parent_id_can_be_set() -> None:
    runtimes = [NodeRuntime(_node("node_1"))]
    ctrl = Controller(
        id="ctrl_child",
        allocator=_AlwaysFirstCandidateAllocator(),
        allocator_type="test",
        managed_nodes=runtimes,
        parent_id="ctrl_parent",
    )
    assert ctrl.parent_id == "ctrl_parent"


# ===========================================================================
# submit()
# ===========================================================================


def test_submit_records_outcome_with_decision_time_fields() -> None:
    ctrl = _make_controller(allocator_type="test_strategy")
    task = _task("t_001", source="node_1", data_size=2.0, cpu_demand=3.0)
    outcome = ctrl.submit(task, t=5.0, estimator=instant_estimator())

    assert outcome.task_id == "t_001"
    assert outcome.decision_time == 5.0
    assert outcome.allocator_type == "test_strategy"
    assert outcome.selected_node == "node_1"
    assert outcome.estimated_completion_time > 5.0
    assert outcome.actual_completion_time is None
    assert outcome.deadline_met is None


def test_submit_stores_outcome_in_controller() -> None:
    ctrl = _make_controller()
    task = _task("t_001")
    ctrl.submit(task, t=0.0, estimator=instant_estimator())
    assert ctrl.has_task("t_001")
    assert ctrl.outcomes["t_001"].selected_node == "node_1"


def test_submit_does_not_enqueue_task() -> None:
    ctrl = _make_controller()
    runtime_for_node_1 = next(
        rt for rt in ctrl.managed_nodes if rt.node_id == "node_1"
    )
    ctrl.submit(_task("t_001"), t=0.0, estimator=instant_estimator())
    assert runtime_for_node_1.queue_length == 0


def test_submit_rejects_unknown_node_from_allocator() -> None:
    ctrl = _make_controller(allocator=_AlwaysGhostAllocator())
    with pytest.raises(RuntimeError, match="unknown node_id 'ghost_node'"):
        ctrl.submit(_task("t_001"), t=0.0, estimator=instant_estimator())


# ===========================================================================
# record_completion()
# ===========================================================================


def test_record_completion_fills_actual_and_deadline_met_true() -> None:
    ctrl = _make_controller()
    task = _task("t_001", deadline=10.0)
    ctrl.submit(task, t=0.0, estimator=instant_estimator())
    ctrl.record_completion(task, completion_time=4.5)
    outcome = ctrl.outcomes["t_001"]
    assert outcome.actual_completion_time == 4.5
    assert outcome.deadline_met is True


def test_record_completion_fills_deadline_met_false_when_late() -> None:
    ctrl = _make_controller()
    task = _task("t_001", deadline=3.0)
    ctrl.submit(task, t=0.0, estimator=instant_estimator())
    ctrl.record_completion(task, completion_time=5.5)
    outcome = ctrl.outcomes["t_001"]
    assert outcome.actual_completion_time == 5.5
    assert outcome.deadline_met is False


def test_record_completion_exactly_at_deadline_counts_as_met() -> None:
    ctrl = _make_controller()
    task = _task("t_001", deadline=4.0)
    ctrl.submit(task, t=0.0, estimator=instant_estimator())
    ctrl.record_completion(task, completion_time=4.0)
    assert ctrl.outcomes["t_001"].deadline_met is True


def test_record_completion_unknown_task_raises() -> None:
    ctrl = _make_controller()
    with pytest.raises(KeyError, match="not tracked"):
        ctrl.record_completion(_task("ghost_task"), completion_time=1.0)


# ===========================================================================
# End-to-end with the real scaffold allocator
# ===========================================================================


def test_with_local_first_helper_offload_keeps_local_when_free() -> None:
    runtimes = [
        NodeRuntime(_node("node_1", "source")),
        NodeRuntime(_node("node_2", "helper")),
    ]
    ctrl = Controller(
        id="ctrl",
        allocator=LocalFirstHelperOffloadAllocator(max_local_queue=3),
        allocator_type="local_first_helper_offload",
        managed_nodes=runtimes,
    )
    outcome = ctrl.submit(
        _task("t_001", source="node_1"), t=0.0, estimator=instant_estimator()
    )
    assert outcome.selected_node == "node_1"


def test_with_local_first_helper_offload_offloads_when_saturated() -> None:
    runtimes = [
        NodeRuntime(_node("node_1", "source")),
        NodeRuntime(_node("node_2", "helper")),
    ]
    ctrl = Controller(
        id="ctrl",
        allocator=LocalFirstHelperOffloadAllocator(max_local_queue=2),
        allocator_type="local_first_helper_offload",
        managed_nodes=runtimes,
    )
    # First two stay local, third hits the threshold and offloads.
    est = instant_estimator()
    rt1 = next(rt for rt in runtimes if rt.node_id == "node_1")
    for tid in ("t_001", "t_002"):
        o = ctrl.submit(_task(tid, source="node_1"), t=0.0, estimator=est)
        rt1.enqueue(_task(tid, source="node_1"))
        assert o.selected_node == "node_1"
    outcome = ctrl.submit(_task("t_003", source="node_1"), t=0.0, estimator=est)
    assert outcome.selected_node == "node_2"
