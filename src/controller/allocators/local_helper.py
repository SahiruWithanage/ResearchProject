"""Local-first / helper-offload allocator. Phase 1 scaffold rule, not a baseline."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.models import EdgeNode, NodeState, Task


@allocators.register("local_first_helper_offload")
class LocalFirstHelperOffloadAllocator(Allocator):
    """Phase 1 scaffold rule: keep tasks on their source node until the
    source's queue reaches ``max_local_queue``, then offload to the
    helper with the shortest queue (ties broken by ``node_id``).

    Exists only to demonstrate the allocate -> enqueue -> process -> log
    pipeline with visible local-vs-offload decisions. **Not a methodology
    baseline**, the named baselines arrive in Stage 5.
    """

    def __init__(self, max_local_queue: int = 3) -> None:
        if max_local_queue < 0:
            raise ValueError(f"max_local_queue must be >= 0, got {max_local_queue}")
        self.max_local_queue = int(max_local_queue)

    def allocate(
        self,
        task: Task,
        candidates: Sequence[EdgeNode],
        states: Mapping[str, NodeState],
        t: float,
    ) -> str:
        if not candidates:
            raise ValueError("allocate() requires at least one candidate")

        # Keep local if the source is a candidate and has room.
        source_id = task.source_node_id
        candidate_by_id = {n.node_id: n for n in candidates}
        if source_id is not None and source_id in candidate_by_id:
            if states[source_id].queue_length < self.max_local_queue:
                return source_id

        # Otherwise offload: prefer helpers, then shortest queue, then node_id (deterministic).
        def sort_key(node: EdgeNode) -> tuple[bool, int, str]:
            is_not_helper = node.node_type != "helper"
            queue = states[node.node_id].queue_length
            return (is_not_helper, queue, node.node_id)

        return min(candidates, key=sort_key).node_id
