"""NodeRuntime: per-node queue, active set, and work-advancement logic."""

from __future__ import annotations
import math
from dataclasses import dataclass
from src.models import EdgeNode, NodeState, Task


@dataclass(slots=True)
class _ActiveTask:
    task: Task
    remaining_work: float


class NodeRuntime:
    """Mutable per-node runtime state during a simulation.

    Wraps a static :class:`EdgeNode` with the queue, active set, and
    work-advancement logic:

    - ``K = max(1, floor(cpu_capacity))`` parallel workers.
    - Each worker drains 1.0 work units per simulated second.
    - Each task carries ``cpu_demand`` work units (``data_size`` is for transmission).
    - Queue discipline is FIFO, a freed worker takes the next queued task.
    """

    def __init__(self, node: EdgeNode) -> None:
        self.node = node
        # K = max(1, floor(cpu_capacity)) parallel workers, each draining 1 unit/sec.
        self.workers: int = max(1, math.floor(node.cpu_capacity))
        self._active: list[_ActiveTask] = []
        self._queue: list[Task] = []

    @property
    def node_id(self) -> str:
        return self.node.node_id

    @property
    def queue_length(self) -> int:
        """Total tasks in the system: active + waiting."""
        return len(self._active) + len(self._queue)

    @property
    def active_count(self) -> int:
        return len(self._active)

    def enqueue(self, task: Task) -> None:
        self._queue.append(task)
        self._fill_active_slots()

    def advance(self, dt: float, t_start: float) -> list[tuple[Task, float]]:
        """Drain `dt` units from each active task. Return tasks that finished this tick."""
        if dt <= 0:
            raise ValueError(f"dt must be > 0, got {dt}")

        completed: list[tuple[Task, float]] = []
        still_active: list[_ActiveTask] = []
        for entry in self._active:
            if entry.remaining_work <= dt:
                # Sub-tick-accurate completion time so deadline checks aren't lossy.
                completion_time = t_start + entry.remaining_work
                completed.append((entry.task, completion_time))
            else:
                entry.remaining_work -= dt
                still_active.append(entry)
        self._active = still_active
        completed.sort(key=lambda pair: (pair[1], pair[0].task_id))

        self._fill_active_slots()
        return completed

    def snapshot(self, t: float) -> NodeState:
        memory_used = sum(e.task.memory_demand for e in self._active)
        memory_util = (
            memory_used / self.node.memory_capacity
            if self.node.memory_capacity > 0
            else 0.0
        )
        cpu_util = (
            len(self._active) / self.node.cpu_capacity
            if self.node.cpu_capacity > 0
            else 0.0
        )
        return NodeState(
            time_step=t,
            node_id=self.node.node_id,
            queue_length=self.queue_length,
            active_tasks=len(self._active),
            cpu_utilisation=cpu_util,
            memory_utilisation=memory_util,
        )

    def _fill_active_slots(self) -> None:
        while len(self._active) < self.workers and self._queue:
            task = self._queue.pop(0)
            work = task.cpu_demand
            self._active.append(_ActiveTask(task=task, remaining_work=work))
