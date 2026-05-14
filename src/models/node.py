"""EdgeNode: static configuration of one node (capacity, tier, role)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EdgeNode:
    """Static description of one compute node. Capacity-related fields don't
    change during a run, per-tick state lives in :class:`NodeState`.

    Fields:
        node_id: unique identifier.
        node_type: ``"source"`` (generates tasks) or ``"helper"`` (only receives offloads).
        cpu_capacity: number of parallel work units the node can run, ``floor()`` is taken to get the worker count.
        memory_capacity: total memory available, in abstract units.
        tier: where the node lives in the topology (``"edge"`` for Phase 1, ``"fog"``/``"cloud"`` later).
    """

    node_id: str
    node_type: str
    cpu_capacity: float
    memory_capacity: float
    tier: str
