"""Zero-delay network (backward compatible with Phase 1)."""

from __future__ import annotations

from src.config.factory import network_models
from src.models import Task
from src.network.base import NetworkModel


@network_models.register("instant")
class InstantNetworkModel(NetworkModel):
    """No transmission delay; tasks arrive at the executor immediately."""

    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        return 0.0

    def expected_uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        return 0.0
