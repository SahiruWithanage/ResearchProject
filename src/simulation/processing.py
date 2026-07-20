"""NodeRuntime: per-node queue, active set, and work-advancement logic."""

from __future__ import annotations
import math
from dataclasses import dataclass
from src.models import EdgeNode, NodeState, Task

_MEMORY_EPS = 1e-12  # float-tolerance for memory-fit comparisons


@dataclass(slots=True)
class _ActiveTask:
    task: Task
    remaining_work: float


class NodeRuntime:
    """Mutable per-node runtime state during a simulation.

    Wraps a static :class:`EdgeNode` with the queue, active set, and
    work-advancement logic:

    - ``K = max(1, floor(cpu_capacity))`` parallel workers.
    - Each worker drains ``cpu_speed`` work units per simulated second
      (1.0 = reference speed; a 0.5-speed node takes twice as long).
    - Each task carries ``cpu_demand`` work units (``data_size`` is for transmission).
    - Queue discipline is FIFO. A queued task is promoted to a free worker
      only if its ``memory_demand`` fits in the remaining memory; if the
      head task doesn't fit, it waits (head-of-line, keeps FIFO honest).
    - ``queue_limit`` (if set) caps active + waiting; see :meth:`has_room`.
      Enforced at *allocation* time by the Controller, not by ``enqueue`` —
      tasks already in transit are accepted on arrival even if the node
      filled up meanwhile (the consequences of stale decisions are a
      Stage 8 topic).
    """

    def __init__(self, node: EdgeNode) -> None:
        self.node = node
        # K = max(1, floor(cpu_capacity)) parallel workers.
        self.workers: int = max(1, math.floor(node.cpu_capacity))
        self.speed: float = float(node.cpu_speed)
        if self.speed <= 0:
            raise ValueError(
                f"node {node.node_id!r} cpu_speed must be > 0, got {self.speed}"
            )
        self._active: list[_ActiveTask] = []
        self._queue: list[Task] = []
        # Stage 8 instability state, driven by scenarios.
        self.failure_state: str = "normal"
        self.reliability_score: float = 1.0
        self._recovery_speed_factor: float = 1.0

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

    def has_room(self) -> bool:
        """True if the node may be *allocated* another task right now."""
        limit = self.node.queue_limit
        return limit is None or self.queue_length < limit

    def is_available(self) -> bool:
        """False while failed: not a valid execution candidate."""
        return self.failure_state != "failed"

    def fail(self) -> list[Task]:
        """Crash the node. Evicts and returns every held task (they are
        lost — a crashed machine doesn't keep its queue)."""
        self.failure_state = "failed"
        evicted = [entry.task for entry in self._active] + list(self._queue)
        self._active.clear()
        self._queue.clear()
        return evicted

    def begin_recovery(self, speed_factor: float = 1.0) -> None:
        """Come back up, optionally at reduced speed while recovering."""
        if speed_factor <= 0:
            raise ValueError(f"speed_factor must be > 0, got {speed_factor}")
        self.failure_state = "recovering" if speed_factor < 1.0 else "normal"
        self._recovery_speed_factor = float(speed_factor)

    def restore(self) -> None:
        """Fully healthy again."""
        self.failure_state = "normal"
        self._recovery_speed_factor = 1.0

    @property
    def effective_speed(self) -> float:
        return self.speed * self._recovery_speed_factor

    def enqueue(self, task: Task) -> None:
        self._queue.append(task)
        self._fill_active_slots()

    def advance(self, dt: float, t_start: float) -> list[tuple[Task, float]]:
        """Drain `dt * effective_speed` from each active task. Return finishers."""
        if dt <= 0:
            raise ValueError(f"dt must be > 0, got {dt}")
        if self.failure_state == "failed":
            return []  # a crashed node does no work

        speed = self.effective_speed
        drained = dt * speed
        completed: list[tuple[Task, float]] = []
        still_active: list[_ActiveTask] = []
        for entry in self._active:
            if entry.remaining_work <= drained:
                # Sub-tick-accurate completion time so deadline checks aren't lossy.
                completion_time = t_start + entry.remaining_work / speed
                completed.append((entry.task, completion_time))
            else:
                entry.remaining_work -= drained
                still_active.append(entry)
        self._active = still_active
        completed.sort(key=lambda pair: (pair[1], pair[0].task_id))

        self._fill_active_slots()
        return completed

    def snapshot(self, t: float) -> NodeState:
        memory_used = self._memory_used()
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
            reliability_score=self.reliability_score,
            failure_state=self.failure_state,
        )

    def _memory_used(self) -> float:
        return sum(e.task.memory_demand for e in self._active)

    def _fill_active_slots(self) -> None:
        while len(self._active) < self.workers and self._queue:
            task = self._queue[0]
            fits = (
                self._memory_used() + task.memory_demand
                <= self.node.memory_capacity + _MEMORY_EPS
            )
            if not fits:
                # Head-of-line wait: memory frees up when active tasks finish.
                break
            self._queue.pop(0)
            self._active.append(_ActiveTask(task=task, remaining_work=task.cpu_demand))
