"""Weighted-score baseline: min weighted sum of delay, load, compute, energy."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import EdgeNode


@allocators.register("weighted_score")
class WeightedScoreAllocator(Allocator):
    """Choose the candidate with the lowest weighted score.

    Score components (all deterministic expectations, so no randomness is
    consumed; states come from the controller's observability model):

    - **delay**: expected network time — uplink + result downlink, seconds.
    - **load**: expected queue wait on the node, seconds.
    - **compute**: expected service time (``cpu_demand / cpu_speed``),
      seconds — captures how *slow* the node is for this task.
    - **energy**: ``energy_cost_factor * compute seconds`` — penalises
      burning time on expensive nodes. Off by default (weight 0).

    ``score = w_delay*delay + w_load*load + w_compute*compute + w_energy*energy``

    With the default weights (1, 1, 1, 0) the score equals the estimated
    completion delay, i.e. "pick the node expected to finish soonest".
    Ties break on ``node_id`` for determinism.
    """

    def __init__(
        self,
        *,
        w_delay: float = 1.0,
        w_load: float = 1.0,
        w_compute: float = 1.0,
        w_energy: float = 0.0,
    ) -> None:
        weights = {
            "w_delay": w_delay,
            "w_load": w_load,
            "w_compute": w_compute,
            "w_energy": w_energy,
        }
        for name, value in weights.items():
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if all(value == 0 for value in weights.values()):
            raise ValueError("at least one weight must be > 0")
        self.w_delay = float(w_delay)
        self.w_load = float(w_load)
        self.w_compute = float(w_compute)
        self.w_energy = float(w_energy)

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")

        task = context.task
        source = task.source_node_id or ""
        estimator = context.estimator

        def score(node: EdgeNode) -> tuple[float, str]:
            state = context.states[node.node_id]
            if source:
                delay = estimator.uplink_delay(
                    source, node.node_id, task, context.t
                ) + estimator.downlink_delay(
                    node.node_id, source, task, context.t
                )
            else:
                delay = 0.0
            load = estimator.queue_wait_proxy(state, task, node)
            compute = estimator.compute_duration(task, node)
            energy = node.energy_cost_factor * compute
            total = (
                self.w_delay * delay
                + self.w_load * load
                + self.w_compute * compute
                + self.w_energy * energy
            )
            return (total, node.node_id)

        return min(context.candidates, key=score).node_id
