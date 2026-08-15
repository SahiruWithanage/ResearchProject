"""Helpers for allocator unit tests (avoids conftest import cycles)."""

from __future__ import annotations

from src.controller.context import DecisionContext
from src.models import EdgeNode, NodeState, Task
from src.network.instant import InstantNetworkModel
from src.simulation.estimates import CompletionEstimator


def instant_estimator() -> CompletionEstimator:
    return CompletionEstimator(InstantNetworkModel())


def decision_context(
    task: Task,
    candidates: list[EdgeNode],
    states: dict[str, NodeState],
    t: float = 0.0,
    rng=None,
) -> DecisionContext:
    return DecisionContext(
        task=task,
        candidates=candidates,
        states=states,
        t=t,
        estimator=instant_estimator(),
        rng=rng,
    )
