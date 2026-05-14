"""Poisson task generator: arrivals at mean rate `rate` per simulated second."""

from __future__ import annotations
import numpy as np
from src.config.factory import generators
from src.generation.base import TaskGenerator
from src.models import Task


@generators.register("poisson")
class PoissonGenerator(TaskGenerator):
    """Stochastic task generator with Poisson(``rate``) arrivals.

    Within each ``emit(t_start, t_end)`` window, the count of arrivals is
    drawn from ``Poisson(rate * window)`` and each arrival is placed
    uniformly inside the window. The generator owns its own
    :class:`numpy.random.Generator` so two runs with the same seed produce
    identical task streams.
    """

    def __init__(
        self,
        *,
        rate: float,
        source_node_id: str,
        rng: np.random.Generator | None = None,
        task_type: str = "compute",
        data_size: float = 1.0,
        cpu_demand: float = 1.0,
        memory_demand: float = 1.0,
        deadline_offset: float = 5.0,
        priority: int = 1,
    ) -> None:
        if rng is None:
            raise ValueError(
                "PoissonGenerator requires an rng (numpy.random.Generator); "
                "the Environment builder injects one per source from the seed"
            )
        if rate < 0:
            raise ValueError(f"rate must be >= 0, got {rate}")
        if deadline_offset <= 0:
            raise ValueError(f"deadline_offset must be > 0, got {deadline_offset}")

        self.rate = float(rate)
        self.source_node_id = source_node_id
        self.rng = rng
        self.task_type = task_type
        self.data_size = float(data_size)
        self.cpu_demand = float(cpu_demand)
        self.memory_demand = float(memory_demand)
        self.deadline_offset = float(deadline_offset)
        self.priority = int(priority)

        self._counter = 0

    def emit(self, t_start: float, t_end: float) -> list[Task]:
        if t_end < t_start:
            raise ValueError(
                f"t_end must be >= t_start, got t_start={t_start}, t_end={t_end}"
            )
        window = t_end - t_start
        if window == 0.0 or self.rate == 0.0:
            return []

        # Draw count from Poisson(rate * window), place tasks at uniform times in [t_start, t_end).
        n = int(self.rng.poisson(self.rate * window))
        if n == 0:
            return []

        arrivals = self.rng.uniform(t_start, t_end, size=n)
        arrivals.sort()

        return [self._build_task(float(t)) for t in arrivals]

    def _build_task(self, arrival_time: float) -> Task:
        self._counter += 1
        return Task(
            task_id=f"{self.source_node_id}_{self._counter:06d}",
            arrival_time=arrival_time,
            task_type=self.task_type,
            data_size=self.data_size,
            cpu_demand=self.cpu_demand,
            memory_demand=self.memory_demand,
            deadline=arrival_time + self.deadline_offset,
            priority=self.priority,
            source_node_id=self.source_node_id,
        )
