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

    def compute_duration(self, task: Task, node: EdgeNode) -> float:
        """Seconds of CPU work on ``node`` (parallel workers drain 1 unit/sec each)."""
        workers = max(1, math.floor(node.cpu_capacity))
        return task.cpu_demand / workers

    def queue_wait_proxy(
        self,
        state: NodeState,
        task: Task,
        node: EdgeNode,
    ) -> float:
        """Each task already in the system adds one average service interval."""
        workers = max(1, math.floor(node.cpu_capacity))
        service = task.cpu_demand / workers
        return state.queue_length * service

    def estimated_completion(
        self,
        source_id: str,
        target: EdgeNode,
        task: Task,
        states: Mapping[str, NodeState],
        t: float,
    ) -> float:
        """Estimated finish time if the task were placed on ``target`` now."""
        state = states[target.node_id]
        transfer = self.uplink_delay(source_id, target.node_id, task, t)
        wait = self.queue_wait_proxy(state, task, target)
        compute = self.compute_duration(task, target)
        return t + transfer + wait + compute
