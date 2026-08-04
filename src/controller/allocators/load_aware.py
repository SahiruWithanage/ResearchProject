"""Load-aware baseline: prefer the node whose backlog clears soonest."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import EdgeNode


@allocators.register("load_aware")
class LoadAwareAllocator(Allocator):
    """Choose the candidate with the smallest *backlog time*.

    Backlog time is the node's remaining work divided by its throughput
    (``workers * cpu_speed``): how long the work already there takes to
    clear. This is the load-aware baseline the methodology calls for -
    load measured in seconds of work, not in task count.

    Counting tasks instead, as this used to, is blind twice over: it
    cannot tell one long job from several short ones, and it ignores node
    speed entirely, so a queue of three on a fast server looks worse than
    a queue of two on a half-speed sensor. That made it a weak opponent,
    and beating a weak opponent proves little.

    Ties break on lower uplink delay (when a source is known), then
    ``node_id`` for determinism.
    """

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")

        source = context.task.source_node_id or ""
        estimator = context.estimator

        def sort_key(node: EdgeNode) -> tuple[float, float, str]:
            state = context.states[node.node_id]
            backlog = estimator.queue_wait_proxy(state, context.task, node)
            uplink = (
                estimator.uplink_delay(source, node.node_id, context.task, context.t)
                if source
                else 0.0
            )
            return (backlog, uplink, node.node_id)

        return min(context.candidates, key=sort_key).node_id
