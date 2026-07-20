"""Poisson task generator: constant-rate or time-varying arrivals."""

from __future__ import annotations

from typing import Any

import numpy as np

from src.config.factory import generators
from src.generation.base import TaskGenerator
from src.generation.rate_patterns import build_rate_pattern
from src.generation.task_builder import TaskBuilder
from src.models import Task


@generators.register("poisson")
class PoissonGenerator(TaskGenerator):
    """Stochastic task generator with Poisson arrivals.

    ``rate`` is either a number (homogeneous Poisson — the count in each
    ``emit`` window is Poisson(rate * window), placed uniformly) or a rate
    pattern spec (``sinusoidal``, ``piecewise``, ...) — then arrivals form
    a non-homogeneous Poisson process, realized by *thinning*: candidates
    are drawn at the pattern's max rate and each is kept with probability
    ``rate(t) / max_rate``.

    Task properties (constants, distribution specs, or a ``task_mix``) are
    handled by :class:`TaskBuilder`. The generator owns its own seeded rng
    stream, so two runs with the same seed produce identical task streams.
    """

    def __init__(
        self,
        *,
        rate: Any,
        source_node_id: str,
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
        if rng is None:
            raise ValueError(
                "PoissonGenerator requires an rng (numpy.random.Generator); "
                "the Environment builder injects one per source from the seed"
            )
        self.rate_pattern = build_rate_pattern(rate)
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
        self._counter = 0

    def emit(self, t_start: float, t_end: float) -> list[Task]:
        if t_end < t_start:
            raise ValueError(
                f"t_end must be >= t_start, got t_start={t_start}, t_end={t_end}"
            )
        window = t_end - t_start
        if window == 0.0:
            return []

        pattern = self.rate_pattern
        if pattern.is_constant:
            # Homogeneous path: identical draw sequence to the Phase 1
            # implementation, so constant-rate configs keep their streams.
            rate = pattern.max_rate()
            if rate == 0.0:
                return []
            n = int(self.rng.poisson(rate * window))
            if n == 0:
                return []
            arrivals = self.rng.uniform(t_start, t_end, size=n)
            arrivals.sort()
            return [self._build_task(float(t)) for t in arrivals]

        # Non-homogeneous path (thinning): candidates at max_rate, each
        # kept with probability rate(t)/max_rate.
        max_rate = pattern.max_rate()
        if max_rate <= 0.0:
            return []
        n = int(self.rng.poisson(max_rate * window))
        if n == 0:
            return []
        candidates = self.rng.uniform(t_start, t_end, size=n)
        candidates.sort()
        tasks: list[Task] = []
        for t in candidates:
            if float(self.rng.random()) < pattern.rate(float(t)) / max_rate:
                tasks.append(self._build_task(float(t)))
        return tasks

    def _build_task(self, arrival_time: float) -> Task:
        self._counter += 1
        return self.builder.build(
            task_id=f"{self.source_node_id}_{self._counter:06d}",
            arrival_time=arrival_time,
            rng=self.rng,
        )
