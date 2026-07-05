"""Allocator: contract for any task-to-node placement strategy."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.controller.context import DecisionContext


class Allocator(ABC):
    """Pluggable allocation strategy.

    Implement :meth:`allocate` to choose a node. Use
    :class:`~src.controller.context.DecisionContext` for node states,
    simulated time, and :class:`~src.simulation.estimates.CompletionEstimator`.
    """

    @abstractmethod
    def allocate(self, context: DecisionContext) -> str:
        """Return the ``node_id`` of the chosen candidate."""
