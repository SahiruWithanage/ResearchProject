"""TaskGenerator: contract for any source of tasks."""

from __future__ import annotations
from abc import ABC, abstractmethod
from src.models import Task


class TaskGenerator(ABC):
    """Pluggable source of tasks. Implement :meth:`emit` to define your arrival pattern.

    Both stochastic generators (Poisson) and deterministic ones
    (fixed-interval, future trace replay) fit this contract.
    """

    @abstractmethod
    def emit(self, t_start: float, t_end: float) -> list[Task]:
        """Return tasks whose arrival_time falls in [t_start, t_end)."""
