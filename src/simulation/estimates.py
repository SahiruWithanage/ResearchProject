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
        it up — only the node's ``cpu_speed`` does. (Historical note: this
        used to divide by the worker count, which didn't match the engine.)
        """
        return task.cpu_demand / node.cpu_speed

    def queue_wait_proxy(
        self,
        state: NodeState,
        task: Task,
        node: EdgeNode,
    ) -> float:
        """Rough queue-wait estimate: tasks ahead / node throughput.

        Throughput is ``workers * cpu_speed`` work units per second; each
        queued task is assumed to carry this task's ``cpu_demand``.
        """
        workers = max(1, math.floor(node.cpu_capacity))
        service = task.cpu_demand / (workers * node.cpu_speed)
        return state.queue_length * service

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
        the expected downlink — matching how ``deadline_met`` is judged.
        """
        state = states[target.node_id]
        transfer = self.uplink_delay(source_id, target.node_id, task, t)
        wait = self.queue_wait_proxy(state, task, target)
        compute = self.compute_duration(task, target)
        result_return = self.downlink_delay(target.node_id, source_id, task, t)
        return t + transfer + wait + compute + result_return
