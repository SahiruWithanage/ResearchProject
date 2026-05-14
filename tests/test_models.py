"""Tests for the data models: construction, defaults, frozen vs mutable."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.models import AllocationOutcome, EdgeNode, NodeState, Task


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

def test_task_basic_construction() -> None:
    task = Task(
        task_id="t_001",
        arrival_time=0.0,
        task_type="sensing",
        data_size=1.5,
        cpu_demand=2.0,
        memory_demand=1.0,
        deadline=5.0,
        priority=1,
    )
    assert task.task_id == "t_001"
    assert task.arrival_time == 0.0
    assert task.task_type == "sensing"
    assert task.priority == 1


def test_task_source_timestamp_defaults_to_none() -> None:
    task = Task(
        task_id="t_002",
        arrival_time=1.0,
        task_type="sensing",
        data_size=1.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=5.0,
        priority=1,
    )
    assert task.source_timestamp is None


def test_task_source_node_id_defaults_to_none() -> None:
    task = Task(
        task_id="t_004",
        arrival_time=1.0,
        task_type="sensing",
        data_size=1.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=5.0,
        priority=1,
    )
    assert task.source_node_id is None


def test_task_source_node_id_can_be_set() -> None:
    task = Task(
        task_id="t_005",
        arrival_time=1.0,
        task_type="sensing",
        data_size=1.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=5.0,
        priority=1,
        source_node_id="node_1",
    )
    assert task.source_node_id == "node_1"


def test_task_source_timestamp_can_be_set() -> None:
    task = Task(
        task_id="t_003",
        arrival_time=12.3,
        task_type="actuation",
        data_size=2.0,
        cpu_demand=3.0,
        memory_demand=2.0,
        deadline=20.0,
        priority=2,
        source_timestamp=1_715_000_000.0,
    )
    assert task.source_timestamp == 1_715_000_000.0


def test_task_is_frozen() -> None:
    task = Task(
        task_id="t_004",
        arrival_time=0.0,
        task_type="sensing",
        data_size=1.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=5.0,
        priority=1,
    )
    with pytest.raises(FrozenInstanceError):
        task.task_id = "t_999"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EdgeNode
# ---------------------------------------------------------------------------

def test_edge_node_basic_construction() -> None:
    node = EdgeNode(
        node_id="node_1",
        node_type="source",
        cpu_capacity=4.0,
        memory_capacity=8.0,
        tier="edge",
    )
    assert node.node_id == "node_1"
    assert node.node_type == "source"
    assert node.cpu_capacity == 4.0
    assert node.tier == "edge"


def test_edge_node_is_frozen() -> None:
    node = EdgeNode(
        node_id="node_1",
        node_type="helper",
        cpu_capacity=4.0,
        memory_capacity=8.0,
        tier="edge",
    )
    with pytest.raises(FrozenInstanceError):
        node.cpu_capacity = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NodeState
# ---------------------------------------------------------------------------

def test_node_state_basic_construction() -> None:
    state = NodeState(
        time_step=5.0,
        node_id="node_1",
        queue_length=2,
        active_tasks=1,
        cpu_utilisation=0.5,
        memory_utilisation=0.3,
    )
    assert state.time_step == 5.0
    assert state.queue_length == 2
    assert state.active_tasks == 1


def test_node_state_uses_float_time() -> None:
    state = NodeState(
        time_step=12.75,
        node_id="node_1",
        queue_length=0,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
    )
    assert state.time_step == 12.75


def test_node_state_is_frozen() -> None:
    state = NodeState(
        time_step=1.0,
        node_id="node_1",
        queue_length=0,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
    )
    with pytest.raises(FrozenInstanceError):
        state.queue_length = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AllocationOutcome
# ---------------------------------------------------------------------------

def test_allocation_outcome_starts_incomplete() -> None:
    outcome = AllocationOutcome(
        task_id="t_001",
        decision_time=1.0,
        allocator_type="local_first_helper_offload",
        selected_node="node_1",
        estimated_completion_time=4.0,
    )
    assert outcome.task_id == "t_001"
    assert outcome.selected_node == "node_1"
    assert outcome.actual_completion_time is None
    assert outcome.deadline_met is None


def test_allocation_outcome_can_be_finalised() -> None:
    outcome = AllocationOutcome(
        task_id="t_001",
        decision_time=1.0,
        allocator_type="local_first_helper_offload",
        selected_node="node_1",
        estimated_completion_time=4.0,
    )
    outcome.actual_completion_time = 4.5
    outcome.deadline_met = False
    assert outcome.actual_completion_time == 4.5
    assert outcome.deadline_met is False
