"""Local-first / helper-offload allocator. Phase 1 scaffold rule, not a baseline."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import EdgeNode


@allocators.register("local_first_helper_offload")
class LocalFirstHelperOffloadAllocator(Allocator):
    """Phase 1 scaffold: local-first until queue threshold, then helper offload.

    Not a methodology baseline. See ``load_aware`` and ``latency_first``.
    """

    def __init__(self, max_local_queue: int = 3) -> None:
        if max_local_queue < 0:
            raise ValueError(f"max_local_queue must be >= 0, got {max_local_queue}")
        self.max_local_queue = int(max_local_queue)

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")

        task = context.task
        states = context.states
        source_id = task.source_node_id
        candidate_by_id = {n.node_id: n for n in context.candidates}

        if source_id is not None and source_id in candidate_by_id:
            if states[source_id].queue_length < self.max_local_queue:
                return source_id

        def sort_key(node: EdgeNode) -> tuple[bool, int, str]:
            is_not_helper = node.node_type != "helper"
            queue = states[node.node_id].queue_length
            return (is_not_helper, queue, node.node_id)

        return min(context.candidates, key=sort_key).node_id
