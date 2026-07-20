"""Fixed-interval task generator: one task every `interval` seconds (deterministic)."""

from __future__ import annotations
import math
import numpy as np
from src.config.factory import generators
from src.generation.base import TaskGenerator
from src.models import Task


@generators.register("fixed_interval")
class FixedIntervalGenerator(TaskGenerator):
    """Deterministic task generator: one task at every ``offset + k * interval``.

    Useful for debugging and tests where you want predictable arrival
    times. Accepts an ``rng`` argument for API uniformity but doesn't use it.
    """

    def __init__(
        self,
        *,
        interval: float,
        source_node_id: str,
        offset: float = 0.0,
        rng: np.random.Generator | None = None,  # unused, accepted for API uniformity
        task_type: str = "compute",
        data_size: float = 1.0,
        cpu_demand: float = 1.0,
        memory_demand: float = 1.0,
        deadline_offset: float = 5.0,
        priority: int = 1,
        gpu_demand: float = 0.0,
        result_size: float = 0.0,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval}")
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        if deadline_offset <= 0:
            raise ValueError(f"deadline_offset must be > 0, got {deadline_offset}")

        self.interval = float(interval)
        self.offset = float(offset)
        self.source_node_id = source_node_id
        self.task_type = task_type
        self.data_size = float(data_size)
        self.cpu_demand = float(cpu_demand)
        self.memory_demand = float(memory_demand)
        self.deadline_offset = float(deadline_offset)
        self.priority = int(priority)
        self.gpu_demand = float(gpu_demand)
        self.result_size = float(result_size)

    def emit(self, t_start: float, t_end: float) -> list[Task]:
        if t_end < t_start:
            raise ValueError(
                f"t_end must be >= t_start, got t_start={t_start}, t_end={t_end}"
            )
        if t_end == t_start:
            return []

        # Smallest k >= 0 with offset + k*interval >= t_start. Counter-based to avoid float drift.
        if self.offset >= t_start:
            k = 0
        else:
            k = math.ceil((t_start - self.offset) / self.interval)
            while self.offset + k * self.interval < t_start:
                k += 1

        tasks: list[Task] = []
        while True:
            arrival = self.offset + k * self.interval
            if arrival >= t_end:
                break
            tasks.append(self._build_task(arrival, k))
            k += 1
        return tasks

    def _build_task(self, arrival_time: float, k: int) -> Task:
        # Index `k` in the id keeps task IDs stable regardless of how emit() is chunked.
        return Task(
            task_id=f"{self.source_node_id}_{k:06d}",
            arrival_time=arrival_time,
            task_type=self.task_type,
            data_size=self.data_size,
            cpu_demand=self.cpu_demand,
            memory_demand=self.memory_demand,
            deadline=arrival_time + self.deadline_offset,
            priority=self.priority,
            source_node_id=self.source_node_id,
            gpu_demand=self.gpu_demand,
            result_size=self.result_size,
        )
