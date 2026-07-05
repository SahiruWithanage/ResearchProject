"""Load-aware baseline: prefer the least-loaded candidate."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import EdgeNode


@allocators.register("load_aware")
class LoadAwareAllocator(Allocator):
    """Choose the candidate with the smallest ``queue_length``.

    Ties break on lower uplink delay (when a source is known), then
    ``node_id`` for determinism.
    """

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")

        source = context.task.source_node_id or ""

        def sort_key(node: EdgeNode) -> tuple[int, float, str]:
            state = context.states[node.node_id]
            uplink = (
                context.estimator.uplink_delay(
                    source, node.node_id, context.task, context.t
                )
                if source
                else 0.0
            )
            return (state.queue_length, uplink, node.node_id)

        return min(context.candidates, key=sort_key).node_id
