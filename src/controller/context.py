"""DecisionContext: what allocators see at decision time."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from src.models import EdgeNode, NodeState, Task
from src.simulation.estimates import CompletionEstimator


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Snapshot passed to every allocator invocation.

    ``candidates`` contains only *eligible* nodes: suitable for the task
    (type / memory / GPU), with queue room, and reachable. The Controller
    filters before calling the allocator, so strategies never need to
    re-check any of that. ``states`` still covers every managed node
    (eligible or not) for allocators that want wider context.

    ``rng`` is a **dedicated stream**, spawned from the run's seed
    separately from the task generators and the network. An allocator may
    draw from it as much as it likes without shifting anyone else's random
    values, which is what keeps the world identical across strategies. It
    is None when the Controller was built without one; allocators that need
    randomness should say so clearly rather than silently going
    deterministic.
    """

    task: Task
    candidates: Sequence[EdgeNode]
    states: Mapping[str, NodeState]
    t: float
    estimator: CompletionEstimator
    rng: np.random.Generator | None = None
