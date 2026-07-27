"""TaskBuilder: shared task-construction logic for all generators.

Generators own *timing* (when tasks arrive); the TaskBuilder owns
*properties* (what each task looks like). Every numeric property accepts a
constant or a distribution spec (see ``distributions.py``), and a source
can emit a weighted mix of task types via ``task_mix``:

.. code-block:: yaml

    generator:
      type: poisson
      rate: 0.6
      deadline_offset: 10.0        # shared default for all profiles
      task_mix:
        - weight: 0.8
          task_type: telemetry
          cpu_demand: 0.5
          data_size: {dist: uniform, low: 50e3, high: 200e3}
        - weight: 0.2
          task_type: analytics
          cpu_demand: {dist: lognormal, mean: 1.0, sigma: 0.4}
          data_size: 2.0e6

Profile fields default to the generator-level values, so the mix only
lists what differs. Constant-only setups consume **no randomness** for
properties - old configs keep byte-identical task streams.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.generation.distributions import Distribution, build_distribution
from src.models import Task

# Numeric per-task properties that accept distribution specs.
_SAMPLED_FIELDS = (
    "data_size",
    "cpu_demand",
    "memory_demand",
    "gpu_demand",
    "result_size",
    "deadline_offset",
)


@dataclass(slots=True)
class _Profile:
    weight: float
    task_type: str
    priority: int
    samplers: dict[str, Distribution]


class TaskBuilder:
    """Builds :class:`Task` objects from (possibly stochastic) profiles."""

    def __init__(
        self,
        *,
        source_node_id: str,
        task_type: str = "compute",
        data_size: Any = 1.0,
        cpu_demand: Any = 1.0,
        memory_demand: Any = 1.0,
        gpu_demand: Any = 0.0,
        result_size: Any = 0.0,
        deadline_offset: Any = 5.0,
        priority: int = 1,
        task_mix: list[dict[str, Any]] | None = None,
    ) -> None:
        self.source_node_id = source_node_id
        defaults: dict[str, Any] = {
            "data_size": data_size,
            "cpu_demand": cpu_demand,
            "memory_demand": memory_demand,
            "gpu_demand": gpu_demand,
            "result_size": result_size,
            "deadline_offset": deadline_offset,
        }

        if task_mix is None:
            self._profiles = [
                self._make_profile({}, defaults, task_type, priority, "generator")
            ]
        else:
            if not isinstance(task_mix, list) or not task_mix:
                raise ValueError("task_mix must be a non-empty list of profiles")
            self._profiles = []
            for i, raw in enumerate(task_mix):
                if not isinstance(raw, Mapping):
                    raise ValueError(f"task_mix[{i}] must be a mapping")
                self._profiles.append(
                    self._make_profile(
                        dict(raw), defaults, task_type, priority, f"task_mix[{i}]"
                    )
                )
        total = sum(p.weight for p in self._profiles)
        if total <= 0:
            raise ValueError("task_mix weights must sum to > 0")
        self._cumulative: list[float] = []
        acc = 0.0
        for p in self._profiles:
            acc += p.weight / total
            self._cumulative.append(acc)
        self._cumulative[-1] = 1.0  # guard against float droop

    @staticmethod
    def _make_profile(
        raw: dict[str, Any],
        defaults: dict[str, Any],
        default_type: str,
        default_priority: int,
        where: str,
    ) -> _Profile:
        known = {"weight", "task_type", "priority", *_SAMPLED_FIELDS}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"{where} has unknown fields {sorted(unknown)}; "
                f"allowed: {sorted(known)}"
            )
        weight = float(raw.get("weight", 1.0))
        if weight < 0:
            raise ValueError(f"{where}.weight must be >= 0, got {weight}")
        samplers = {
            field: build_distribution(
                raw.get(field, defaults[field]), f"{where}.{field}"
            )
            for field in _SAMPLED_FIELDS
        }
        # Fail fast on obviously-wrong constants (sampled values are
        # clamped at build time instead, since a draw can't be pre-checked).
        offset = samplers["deadline_offset"]
        if offset.is_constant and offset.sample(None) <= 0:
            raise ValueError(
                f"{where}.deadline_offset must be > 0, got {offset.sample(None)}"
            )
        for field in ("data_size", "cpu_demand", "memory_demand",
                      "gpu_demand", "result_size"):
            sampler = samplers[field]
            if sampler.is_constant and sampler.sample(None) < 0:
                raise ValueError(
                    f"{where}.{field} must be >= 0, got {sampler.sample(None)}"
                )
        return _Profile(
            weight=weight,
            task_type=str(raw.get("task_type", default_type)),
            priority=int(raw.get("priority", default_priority)),
            samplers=samplers,
        )

    @property
    def needs_rng(self) -> bool:
        """True if building tasks will consume randomness."""
        if len(self._profiles) > 1:
            return True
        return any(
            not sampler.is_constant
            for profile in self._profiles
            for sampler in profile.samplers.values()
        )

    def build(
        self,
        task_id: str,
        arrival_time: float,
        rng: np.random.Generator | None,
    ) -> Task:
        profile = self._pick_profile(rng)
        values = {
            field: sampler.sample(rng)
            for field, sampler in profile.samplers.items()
        }
        # Physical floors: demands can't go negative, deadlines can't be
        # instant no matter what a distribution draws.
        for field in ("data_size", "cpu_demand", "memory_demand",
                      "gpu_demand", "result_size"):
            values[field] = max(0.0, values[field])
        values["cpu_demand"] = max(1e-9, values["cpu_demand"])
        values["deadline_offset"] = max(1e-9, values["deadline_offset"])

        return Task(
            task_id=task_id,
            arrival_time=arrival_time,
            task_type=profile.task_type,
            data_size=values["data_size"],
            cpu_demand=values["cpu_demand"],
            memory_demand=values["memory_demand"],
            deadline=arrival_time + values["deadline_offset"],
            priority=profile.priority,
            source_node_id=self.source_node_id,
            gpu_demand=values["gpu_demand"],
            result_size=values["result_size"],
        )

    def _pick_profile(self, rng: np.random.Generator | None) -> _Profile:
        if len(self._profiles) == 1:
            return self._profiles[0]
        u = float(rng.random())
        for profile, edge in zip(self._profiles, self._cumulative):
            if u <= edge:
                return profile
        return self._profiles[-1]
