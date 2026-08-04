"""Reliability-threshold baseline: avoid nodes that look undependable."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import EdgeNode


@allocators.register("reliability_threshold")
class ReliabilityThresholdAllocator(Allocator):
    """Exclude candidates whose *observed* reliability_score is below
    ``min_reliability``, then behave like load_aware among the survivors.

    Judged on the controller's (possibly stale) observed states - with
    heartbeat observability the controller may keep trusting a node whose
    reliability collapsed after its last report, which is exactly the
    failure mode this baseline exposes. Falls back to all candidates when
    every node looks unreliable (some placement beats certain loss).
    """

    def __init__(self, *, min_reliability: float = 0.5) -> None:
        if not 0.0 <= min_reliability <= 1.0:
            raise ValueError(
                f"min_reliability must be in [0, 1], got {min_reliability}"
            )
        self.min_reliability = float(min_reliability)

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")

        states = context.states
        trusted = [
            n
            for n in context.candidates
            if states[n.node_id].reliability_score >= self.min_reliability
        ]
        pool = trusted or list(context.candidates)

        source = context.task.source_node_id or ""
        estimator = context.estimator

        # Same ordering as load_aware, so the only difference between the two
        # baselines is the reliability filter - which is what this baseline
        # is meant to isolate.
        def sort_key(node: EdgeNode) -> tuple[float, float, str]:
            state = states[node.node_id]
            backlog = estimator.queue_wait_proxy(state, context.task, node)
            uplink = (
                estimator.uplink_delay(source, node.node_id, context.task, context.t)
                if source
                else 0.0
            )
            return (backlog, uplink, node.node_id)

        return min(pool, key=sort_key).node_id
