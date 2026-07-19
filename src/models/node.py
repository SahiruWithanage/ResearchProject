"""EdgeNode: static configuration of one node (capacity, speed, tier, role)."""

from __future__ import annotations
from dataclasses import dataclass

from .task import Task


@dataclass(frozen=True, slots=True)
class EdgeNode:
    """Static description of one compute node. Capacity-related fields don't
    change during a run, per-tick state lives in :class:`NodeState`.

    Fields:
        node_id: unique identifier.
        node_type: ``"source"`` (generates tasks) or ``"helper"`` (only receives offloads).
        cpu_capacity: number of parallel workers, ``floor()`` is taken to get the worker count.
        memory_capacity: total memory available, in abstract units. Enforced:
            the sum of *active* tasks' ``memory_demand`` never exceeds it.
        tier: where the node lives in the topology (``"edge"``, ``"fog"``, ``"cloud"``).
        cpu_speed: work units each worker drains per simulated second.
            1.0 = reference speed; 0.5 = half-speed device (same task takes
            twice as long). This is what makes nodes *feel* different.
        queue_limit: max tasks the node will hold (active + waiting), or
            ``None`` for unlimited. Full nodes are excluded from allocation.
        accepts_task_types: task types this node can execute, or ``None``
            for "accepts everything" (task-suitability heterogeneity).
        gpu_capacity: GPU work units available. 0.0 = no GPU. Only used for
            suitability checks for now; GPU processing is deferred.
        energy_cost_factor: relative energy cost of running work here.
            Carried for the future weighted-score baseline; inert today.
    """

    node_id: str
    node_type: str
    cpu_capacity: float
    memory_capacity: float
    tier: str
    cpu_speed: float = 1.0
    queue_limit: int | None = None
    accepts_task_types: tuple[str, ...] | None = None
    gpu_capacity: float = 0.0
    energy_cost_factor: float = 1.0

    def is_suitable(self, task: Task) -> bool:
        """Static check: could this node *ever* run this task?

        False when the task's type isn't accepted, its memory demand exceeds
        the node's total memory, or it demands GPU work on a GPU-less node.
        (Whether the node currently has room is a runtime question —
        see ``NodeRuntime.has_room``.)
        """
        if (
            self.accepts_task_types is not None
            and task.task_type not in self.accepts_task_types
        ):
            return False
        if task.memory_demand > self.memory_capacity:
            return False
        if task.gpu_demand > 0.0 and self.gpu_capacity <= 0.0:
            return False
        return True
