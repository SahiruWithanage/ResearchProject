"""Allocator: contract for any task-to-node placement strategy."""

from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from src.models import EdgeNode, NodeState, Task


class Allocator(ABC):
    """Pluggable allocation strategy. Implement :meth:`allocate` to choose a node for a task.

    This is the seam through which Stage 5 baselines (latency-first,
    load-aware, weighted-score), the Stage 6 MILP optimum, and the
    Stage 9 Bayesian allocator all plug in.
    """

    @abstractmethod
    def allocate(
        self,
        task: Task,
        candidates: Sequence[EdgeNode],
        states: Mapping[str, NodeState],
        t: float,
    ) -> str:
        """Return the node_id of the candidate chosen for `task`."""
