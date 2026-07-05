"""Latency-first baseline: prefer the lowest uplink delay to the executor."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import EdgeNode


@allocators.register("latency_first")
class LatencyFirstAllocator(Allocator):
    """Choose the candidate with the smallest uplink transmission delay.

    Ties break on shorter ``queue_length``, then ``node_id``.
    Requires ``task.source_node_id`` for meaningful network delays; if
    missing, all uplink delays are treated as zero and tie-breaking reduces
    to load-aware behaviour.
    """

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")

        source = context.task.source_node_id or ""

        def sort_key(node: EdgeNode) -> tuple[float, int, str]:
            state = context.states[node.node_id]
            uplink = (
                context.estimator.uplink_delay(
                    source, node.node_id, context.task, context.t
                )
                if source
                else 0.0
            )
            return (uplink, state.queue_length, node.node_id)

        return min(context.candidates, key=sort_key).node_id
