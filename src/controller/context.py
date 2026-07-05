"""DecisionContext: what allocators see at decision time."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.models import EdgeNode, NodeState, Task
from src.simulation.estimates import CompletionEstimator


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Snapshot passed to every allocator invocation."""

    task: Task
    candidates: Sequence[EdgeNode]
    states: Mapping[str, NodeState]
    t: float
    estimator: CompletionEstimator
