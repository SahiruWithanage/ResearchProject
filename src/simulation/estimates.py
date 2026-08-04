"""Completion time estimates for allocation decisions."""

from __future__ import annotations

import math
from collections.abc import Mapping

from src.models import EdgeNode, NodeState, Task
from src.network.base import NetworkModel


class CompletionEstimator:
    """Shared physics for allocators: uplink + queue proxy + compute."""

    def __init__(self, network: NetworkModel) -> None:
        self.network = network

    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Expected (deterministic) uplink delay for decision-making.

        Deliberately not the sampled delay: estimates may be requested any
        number of times per decision, so they must never consume randomness.
        The realized delay is sampled once, at dispatch, by the Environment.
        """
        return self.network.expected_uplink_delay(source_id, target_id, task, t)

    def downlink_delay(
        self,
        executor_id: str,
        source_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Expected (deterministic) executor -> source result-return delay.

        Zero when the task has no result payload or would run at home.
        """
        if task.result_size <= 0.0 or executor_id == source_id:
            return 0.0
        return self.network.expected_downlink_delay(executor_id, source_id, task, t)

    def compute_duration(self, task: Task, node: EdgeNode) -> float:
        """Seconds this task computes on ``node``.

        A task occupies exactly one worker, so worker *count* doesn't speed
        it up - only the node's ``cpu_speed`` does. (Historical note: this
        used to divide by the worker count, which didn't match the engine.)
        """
        return task.cpu_demand / node.cpu_speed

    @staticmethod
    def throughput(node: EdgeNode) -> float:
        """Work units this node clears per second at full occupancy."""
        workers = max(1, math.floor(node.cpu_capacity))
        return workers * node.cpu_speed

    def queue_wait_proxy(
        self,
        state: NodeState,
        task: Task,
        node: EdgeNode,
    ) -> float:
        """How long the work already on this node takes to clear.

        Uses the node's reported *remaining work*, not its task count. The
        old version assumed every queued task was the same size as the one
        being placed, which under a mixed workload underestimated the wait
        for small tasks and overestimated it for large ones - a bias that
        would have been silently corrected by any allocator with better
        queue reasoning, making it look smarter than it was.

        ``task`` is unused now but kept in the signature: per-task
        estimates (priority, class-aware queueing) are a natural extension.
        """
        return state.queued_work / self.throughput(node)

    def estimated_completion(
        self,
        source_id: str,
        target: EdgeNode,
        task: Task,
        states: Mapping[str, NodeState],
        t: float,
    ) -> float:
        """Estimated *result-in-hand* time if placed on ``target`` now.

        Uplink + queue wait + compute + (if a result must travel back)
        the expected downlink - matching how ``deadline_met`` is judged.
        """
        state = states[target.node_id]
        transfer = self.uplink_delay(source_id, target.node_id, task, t)
        wait = self.queue_wait_proxy(state, task, target)
        compute = self.compute_duration(task, target)
        result_return = self.downlink_delay(target.node_id, source_id, task, t)
        return t + transfer + wait + compute + result_return
