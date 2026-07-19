"""DecisionContext: what allocators see at decision time."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.models import EdgeNode, NodeState, Task
from src.simulation.estimates import CompletionEstimator


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Snapshot passed to every allocator invocation.

    ``candidates`` contains only *eligible* nodes: suitable for the task
    (type / memory / GPU) and currently with queue room. The Controller
    filters before calling the allocator, so strategies never need to
    re-check suitability or fullness. ``states`` still covers every managed
    node (eligible or not) for allocators that want wider context.
    """

    task: Task
    candidates: Sequence[EdgeNode]
    states: Mapping[str, NodeState]
    t: float
    estimator: CompletionEstimator
