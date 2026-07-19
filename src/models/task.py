"""Task: one unit of work, immutable once created."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Task:
    """One unit of work submitted to the simulator.

    Fields:
        task_id: unique identifier (e.g. ``"node_1_000042"``).
        arrival_time: when the task entered the system, in simulated seconds.
        task_type: category label (e.g. ``"compute"``), free-form for now.
        data_size: payload size in bytes (drives transmission time only).
        cpu_demand: CPU work units required.
        memory_demand: memory units used while running.
        deadline: absolute simulated time by which the task must finish.
        priority: integer priority. Unused in Phase 1, kept for later stages.
        source_node_id: which node generated this task. Used for routing.
        source_timestamp: original timestamp from a trace dataset, if any.
        gpu_demand: GPU work units required. 0.0 = pure-CPU task. Nodes
            without GPU capacity are unsuitable for tasks that demand GPU;
            GPU *processing* is not modelled yet (Stage 4 defers it).
    """

    task_id: str
    arrival_time: float
    task_type: str
    data_size: float
    cpu_demand: float
    memory_demand: float
    deadline: float
    priority: int
    source_node_id: str | None = None
    source_timestamp: float | None = None
    gpu_demand: float = 0.0
