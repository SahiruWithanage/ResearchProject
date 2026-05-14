"""Contract tests for the TaskGenerator and Allocator ABCs."""

from __future__ import annotations

import pytest

from src.controller.allocators import Allocator
from src.generation import TaskGenerator
from src.models import EdgeNode, NodeState, Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str = "t_001", arrival: float = 0.0) -> Task:
    return Task(
        task_id=task_id,
        arrival_time=arrival,
        task_type="sensing",
        data_size=1.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=arrival + 5.0,
        priority=1,
    )


def _make_node(node_id: str, node_type: str = "source") -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type=node_type,
        cpu_capacity=4.0,
        memory_capacity=8.0,
        tier="edge",
    )


def _make_state(node_id: str, t: float = 0.0) -> NodeState:
    return NodeState(
        time_step=t,
        node_id=node_id,
        queue_length=0,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
    )


# ---------------------------------------------------------------------------
# TaskGenerator
# ---------------------------------------------------------------------------

def test_task_generator_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        TaskGenerator()  # type: ignore[abstract]


def test_task_generator_subclass_without_emit_cannot_be_instantiated() -> None:
    class Incomplete(TaskGenerator):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]


def test_task_generator_subclass_with_emit_can_be_instantiated() -> None:
    class EmptyGenerator(TaskGenerator):
        def emit(self, t_start: float, t_end: float) -> list[Task]:
            return []

    gen = EmptyGenerator()
    assert gen.emit(0.0, 1.0) == []


def test_task_generator_subclass_can_emit_tasks_within_window() -> None:
    class OneAtZero(TaskGenerator):
        def emit(self, t_start: float, t_end: float) -> list[Task]:
            if t_start <= 0.0 < t_end:
                return [_make_task()]
            return []

    gen = OneAtZero()
    first_window = gen.emit(0.0, 1.0)
    assert len(first_window) == 1
    assert first_window[0].arrival_time == 0.0
    assert gen.emit(1.0, 2.0) == []


# ---------------------------------------------------------------------------
# Allocator
# ---------------------------------------------------------------------------

def test_allocator_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        Allocator()  # type: ignore[abstract]


def test_allocator_subclass_without_allocate_cannot_be_instantiated() -> None:
    class Incomplete(Allocator):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]


def test_allocator_subclass_with_allocate_can_be_instantiated() -> None:
    class AlwaysFirstCandidate(Allocator):
        def allocate(self, task, candidates, states, t):
            return candidates[0].node_id

    allocator = AlwaysFirstCandidate()
    task = _make_task()
    candidates = [_make_node("n_1"), _make_node("n_2", node_type="helper")]
    states = {n.node_id: _make_state(n.node_id) for n in candidates}

    chosen = allocator.allocate(task, candidates, states, t=0.0)
    assert chosen == "n_1"


def test_allocator_can_make_a_state_dependent_choice() -> None:
    class ShortestQueueScratch(Allocator):
        def allocate(self, task, candidates, states, t):
            return min(candidates, key=lambda n: states[n.node_id].queue_length).node_id

    allocator = ShortestQueueScratch()
    task = _make_task()
    candidates = [_make_node("n_1"), _make_node("n_2")]
    states = {
        "n_1": NodeState(time_step=0.0, node_id="n_1", queue_length=5,
                          active_tasks=1, cpu_utilisation=0.8,
                          memory_utilisation=0.5),
        "n_2": NodeState(time_step=0.0, node_id="n_2", queue_length=1,
                          active_tasks=0, cpu_utilisation=0.2,
                          memory_utilisation=0.1),
    }

    assert allocator.allocate(task, candidates, states, t=0.0) == "n_2"
