"""Fixed-interval task generator: one task every `interval` seconds."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from src.config.factory import generators
from src.generation.base import TaskGenerator
from src.generation.task_builder import TaskBuilder
from src.models import Task


@generators.register("fixed_interval")
class FixedIntervalGenerator(TaskGenerator):
    """Deterministic arrival times: one task at every ``offset + k * interval``.

    Useful for debugging and tests where you want predictable arrivals.
    Task *properties* may still be stochastic (distribution specs or a
    ``task_mix``) - that requires an ``rng``; with constants only, the
    generator stays fully deterministic and needs none.
    """

    def __init__(
        self,
        *,
        interval: float,
        source_node_id: str,
        offset: float = 0.0,
        rng: np.random.Generator | None = None,
        task_type: str = "compute",
        data_size: Any = 1.0,
        cpu_demand: Any = 1.0,
        memory_demand: Any = 1.0,
        deadline_offset: Any = 5.0,
        priority: int = 1,
        gpu_demand: Any = 0.0,
        result_size: Any = 0.0,
        task_mix: list[dict[str, Any]] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval}")
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")

        self.interval = float(interval)
        self.offset = float(offset)
        self.source_node_id = source_node_id
        self.rng = rng
        self.builder = TaskBuilder(
            source_node_id=source_node_id,
            task_type=task_type,
            data_size=data_size,
            cpu_demand=cpu_demand,
            memory_demand=memory_demand,
            gpu_demand=gpu_demand,
            result_size=result_size,
            deadline_offset=deadline_offset,
            priority=priority,
            task_mix=task_mix,
        )
        if self.builder.needs_rng and rng is None:
            raise ValueError(
                "fixed_interval with stochastic task properties (distribution "
                "specs or a task_mix) requires an rng; the Environment builder "
                "injects one per source from the seed"
            )

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
        return self.builder.build(
            task_id=f"{self.source_node_id}_{k:06d}",
            arrival_time=arrival_time,
            rng=self.rng,
        )
