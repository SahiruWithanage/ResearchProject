"""NodeState: one snapshot of a node's runtime state at a given tick."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeState:
    """A snapshot of one node's runtime state at a single point in time.

    Produced once per snapshot tick for the state log, and on demand for
    the allocator at decision time.

    Fields:
        time_step: simulated time the snapshot was taken.
        node_id: which node this state belongs to.
        queue_length: total tasks in the system: active + waiting.
        queued_work: total *remaining* work units held by the node -
            unfinished work on active tasks plus the full demand of waiting
            ones. Divided by the node's throughput this gives an honest
            backlog time, where ``queue_length`` alone cannot distinguish
            one big task from several small ones. A node genuinely knows
            its own backlog, and it reaches allocators through the same
            observability model as everything else, so staleness still
            applies equally to every strategy.
        active_tasks: how many tasks are currently being worked on.
        cpu_utilisation: ``active_tasks / cpu_capacity``, in [0, 1].
        memory_utilisation: sum of active ``memory_demand`` divided by ``memory_capacity``.
        reliability_score: how dependable the node currently is, in [0, 1].
            1.0 = fully healthy; degraded by Stage 8 scenarios. Purely
            informational for deterministic baselines; input for the
            reliability_threshold allocator and the Bayesian layer.
        failure_state: ``"normal"``, ``"recovering"``, or ``"failed"``.
    """

    time_step: float
    node_id: str
    queue_length: int
    active_tasks: int
    cpu_utilisation: float
    memory_utilisation: float
    reliability_score: float = 1.0
    failure_state: str = "normal"
    queued_work: float = 0.0
