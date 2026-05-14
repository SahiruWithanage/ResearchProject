"""Tests for NodeRuntime: queueing, work draining, completions, snapshots."""

from __future__ import annotations

import math

import pytest

from src.models import EdgeNode, Task
from src.simulation import NodeRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(cpu_capacity: float = 4.0, memory_capacity: float = 8.0) -> EdgeNode:
    return EdgeNode(
        node_id="node_x",
        node_type="source",
        cpu_capacity=cpu_capacity,
        memory_capacity=memory_capacity,
        tier="edge",
    )


def _task(
    task_id: str,
    *,
    arrival: float = 0.0,
    data_size: float = 1.0,
    cpu_demand: float = 1.0,
    memory_demand: float = 1.0,
    deadline: float | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        arrival_time=arrival,
        task_type="compute",
        data_size=data_size,
        cpu_demand=cpu_demand,
        memory_demand=memory_demand,
        deadline=deadline if deadline is not None else arrival + 100.0,
        priority=1,
        source_node_id="node_x",
    )


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_runtime_starts_empty() -> None:
    rt = NodeRuntime(_node())
    assert rt.queue_length == 0
    assert rt.active_count == 0


def test_runtime_workers_floor_of_capacity() -> None:
    rt = NodeRuntime(_node(cpu_capacity=4.7))
    assert rt.workers == 4


def test_runtime_at_least_one_worker() -> None:
    rt = NodeRuntime(_node(cpu_capacity=0.5))
    assert rt.workers == 1


# ---------------------------------------------------------------------------
# Enqueue / promote-to-active
# ---------------------------------------------------------------------------


def test_enqueue_promotes_to_active_when_workers_available() -> None:
    rt = NodeRuntime(_node(cpu_capacity=2.0))
    rt.enqueue(_task("t_001"))
    assert rt.active_count == 1
    assert rt.queue_length == 1  # active + waiting


def test_excess_enqueue_waits_in_queue() -> None:
    rt = NodeRuntime(_node(cpu_capacity=2.0))
    for i in range(5):
        rt.enqueue(_task(f"t_{i}"))
    assert rt.active_count == 2
    assert rt.queue_length == 5


# ---------------------------------------------------------------------------
# Advance: work drain and completions
# ---------------------------------------------------------------------------


def test_advance_drains_work() -> None:
    rt = NodeRuntime(_node(cpu_capacity=2.0))
    rt.enqueue(_task("t_001", data_size=1.0, cpu_demand=3.0))  # 3 units of work
    completed = rt.advance(dt=1.0, t_start=0.0)
    assert completed == []  # 3 units, only drained 1
    assert rt.active_count == 1


def test_task_completes_when_work_exhausted() -> None:
    rt = NodeRuntime(_node(cpu_capacity=1.0))
    rt.enqueue(_task("t_001", data_size=1.0, cpu_demand=1.0))  # 1 unit
    completed = rt.advance(dt=1.0, t_start=0.0)
    assert len(completed) == 1
    task, completion_time = completed[0]
    assert task.task_id == "t_001"
    assert completion_time == 1.0  # exactly at end of tick


def test_completion_time_is_sub_tick_accurate() -> None:
    # 0.3 work units should finish at t_start + 0.3, not at t_end.
    rt = NodeRuntime(_node(cpu_capacity=1.0))
    rt.enqueue(_task("t_001", data_size=0.3, cpu_demand=1.0))
    completed = rt.advance(dt=1.0, t_start=5.0)
    assert len(completed) == 1
    _, completion_time = completed[0]
    assert math.isclose(completion_time, 5.3, abs_tol=1e-9)


def test_completed_tasks_vacate_workers_for_queue() -> None:
    rt = NodeRuntime(_node(cpu_capacity=1.0))
    rt.enqueue(_task("t_001", data_size=1.0, cpu_demand=1.0))
    rt.enqueue(_task("t_002", data_size=2.0, cpu_demand=1.0))
    # Active: t_001 (1.0 work). Queue: t_002 (2.0 work).
    completed = rt.advance(dt=1.0, t_start=0.0)
    assert [t.task_id for t, _ in completed] == ["t_001"]
    # t_002 should now be active.
    assert rt.active_count == 1


def test_multiple_completions_in_one_tick_sorted_by_completion_time() -> None:
    rt = NodeRuntime(_node(cpu_capacity=3.0))
    rt.enqueue(_task("t_001", data_size=0.5, cpu_demand=1.0))
    rt.enqueue(_task("t_002", data_size=0.2, cpu_demand=1.0))
    rt.enqueue(_task("t_003", data_size=0.8, cpu_demand=1.0))
    completed = rt.advance(dt=1.0, t_start=0.0)
    # All three tasks complete in this tick at different times.
    times = [t for _, t in completed]
    ids = [task.task_id for task, _ in completed]
    assert ids == ["t_002", "t_001", "t_003"]
    assert times == sorted(times)


def test_advance_zero_dt_raises() -> None:
    rt = NodeRuntime(_node())
    with pytest.raises(ValueError, match="dt must be > 0"):
        rt.advance(dt=0, t_start=0.0)


def test_advance_with_empty_runtime_returns_nothing() -> None:
    rt = NodeRuntime(_node())
    assert rt.advance(dt=1.0, t_start=0.0) == []


# ---------------------------------------------------------------------------
# Long-running tasks: cpu_demand > 1
# ---------------------------------------------------------------------------


def test_task_with_high_cpu_demand_takes_multiple_ticks() -> None:
    rt = NodeRuntime(_node(cpu_capacity=1.0))
    rt.enqueue(_task("t_001", data_size=1.0, cpu_demand=3.0))  # 3 work units

    assert rt.advance(dt=1.0, t_start=0.0) == []
    assert rt.advance(dt=1.0, t_start=1.0) == []
    completed = rt.advance(dt=1.0, t_start=2.0)
    assert len(completed) == 1
    _, t_complete = completed[0]
    assert t_complete == 3.0


def test_data_size_extends_work() -> None:
    # work_units = data_size * cpu_demand: doubling data_size doubles duration.
    rt = NodeRuntime(_node(cpu_capacity=1.0))
    rt.enqueue(_task("t_001", data_size=2.0, cpu_demand=1.0))  # 2 work units
    assert rt.advance(dt=1.0, t_start=0.0) == []
    completed = rt.advance(dt=1.0, t_start=1.0)
    assert len(completed) == 1


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_snapshot_reports_empty_state() -> None:
    rt = NodeRuntime(_node(cpu_capacity=4.0, memory_capacity=8.0))
    state = rt.snapshot(t=5.0)
    assert state.time_step == 5.0
    assert state.node_id == "node_x"
    assert state.queue_length == 0
    assert state.active_tasks == 0
    assert state.cpu_utilisation == 0.0
    assert state.memory_utilisation == 0.0


def test_snapshot_reports_active_and_queued() -> None:
    rt = NodeRuntime(_node(cpu_capacity=4.0, memory_capacity=8.0))
    for i in range(5):
        rt.enqueue(_task(f"t_{i}", memory_demand=1.0))
    state = rt.snapshot(t=0.0)
    # cpu_capacity=4.0 -> 4 workers, 4 of 5 enqueued go active, 1 waits.
    assert state.active_tasks == 4
    assert state.queue_length == 5
    assert state.cpu_utilisation == 4 / 4.0  # 4 workers used / 4 capacity = 1.0
    assert state.memory_utilisation == 4 * 1.0 / 8.0


def test_snapshot_reports_partial_cpu_utilisation() -> None:
    rt = NodeRuntime(_node(cpu_capacity=4.0, memory_capacity=8.0))
    for i in range(2):
        rt.enqueue(_task(f"t_{i}", memory_demand=1.0))
    state = rt.snapshot(t=0.0)
    assert state.active_tasks == 2
    assert state.queue_length == 2
    assert state.cpu_utilisation == 2 / 4.0  # 2 workers used / 4 capacity = 0.5
    assert state.memory_utilisation == 2 * 1.0 / 8.0


def test_snapshot_after_completion() -> None:
    rt = NodeRuntime(_node(cpu_capacity=1.0))
    rt.enqueue(_task("t_001", data_size=1.0, cpu_demand=1.0))
    rt.advance(dt=1.0, t_start=0.0)
    state = rt.snapshot(t=1.0)
    assert state.queue_length == 0
    assert state.active_tasks == 0
