"""NetworkModel: contract for uplink delay between nodes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Task


class NetworkModel(ABC):
    """Estimates one-way uplink delay from a source node to an executor node.

    Downlink is deferred; see ``resources/DELAY_MODEL.md``.
    """

    @abstractmethod
    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Return uplink duration in simulated seconds (>= 0)."""
