"""Observability models: what the controller *believes* about its nodes.

Until now the controller saw perfect, live node state at every decision.
Real controllers don't: nodes report their state periodically (heartbeats),
the reports take time to travel, and by decision time the picture is stale.
This module makes that gap a pluggable, configurable model.

.. code-block:: yaml

    controllers:
      - id: ctrl_main
        allocator: {type: load_aware}
        observability:
          type: heartbeat
          interval: 1.0        # nodes report once per simulated second
          report_delay: 0.02   # each report takes 20 ms to arrive
        manages: [...]

Scope note (deliberate, see DESIGN.md): the *allocator's scoring
information* (queue lengths etc.) comes from the observability model, but
eligibility/admission (queue limits, suitability) stays physical - the
node itself always knows whether it is full, just like a real machine
refusing a connection. The consequences of acting on stale beliefs (bad
placements) are results to be measured, not errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.config.factory import observability_models
from src.models import NodeState

if TYPE_CHECKING:
    from src.simulation.processing import NodeRuntime


class ObservabilityModel(ABC):
    """Contract: attach to runtimes once, refresh each tick, observe anytime.

    ``refresh(t)`` is called once per tick by the Environment when the
    world state is current; ``observe(t)`` may be called any number of
    times and must be side-effect free.
    """

    def attach(self, runtimes: list[NodeRuntime]) -> None:
        self._runtimes = list(runtimes)

    @abstractmethod
    def refresh(self, t: float) -> None: ...

    @abstractmethod
    def observe(self, t: float) -> dict[str, NodeState]: ...


@observability_models.register("perfect")
class PerfectObservability(ObservabilityModel):
    """The controller sees live truth (Phase 1 behaviour, the default)."""

    def refresh(self, t: float) -> None:
        pass

    def observe(self, t: float) -> dict[str, NodeState]:
        return {rt.node_id: rt.snapshot(t) for rt in self._runtimes}


@observability_models.register("heartbeat")
class HeartbeatObservability(ObservabilityModel):
    """Nodes report every ``interval`` seconds; reports travel for
    ``report_delay`` seconds; the controller sees the latest *arrived*
    report per node.

    Reports are captured at the first tick boundary after their scheduled
    moment (quantization <= dt) and labelled with the scheduled time. The
    initial (t = 0) state is visible from the start - the controller knows
    the world it was configured with.
    """

    def __init__(self, *, interval: float, report_delay: float = 0.0) -> None:
        if interval <= 0:
            raise ValueError(f"heartbeat interval must be > 0, got {interval}")
        if report_delay < 0:
            raise ValueError(
                f"heartbeat report_delay must be >= 0, got {report_delay}"
            )
        self.interval = float(interval)
        self.report_delay = float(report_delay)
        self._visible: dict[str, NodeState] = {}
        self._pending: dict[str, list[tuple[float, NodeState]]] = {}
        self._next_report: dict[str, float] = {}

    def attach(self, runtimes: list[NodeRuntime]) -> None:
        super().attach(runtimes)
        for rt in runtimes:
            self._visible[rt.node_id] = rt.snapshot(0.0)
            self._pending[rt.node_id] = []
            self._next_report[rt.node_id] = self.interval

    def refresh(self, t: float) -> None:
        eps = 1e-12
        for rt in self._runtimes:
            node_id = rt.node_id
            # Capture any reports whose scheduled moment has passed. A
            # failed node sends nothing - its heartbeat simply goes silent
            # and the controller's belief freezes at the last report.
            while self._next_report[node_id] <= t + eps:
                scheduled = self._next_report[node_id]
                if rt.failure_state != "failed":
                    snapshot = rt.snapshot(scheduled)  # current state, scheduled label
                    self._pending[node_id].append(
                        (scheduled + self.report_delay, snapshot)
                    )
                self._next_report[node_id] = scheduled + self.interval
            # Promote reports that have arrived at the controller.
            arrived = [p for p in self._pending[node_id] if p[0] <= t + eps]
            if arrived:
                self._visible[node_id] = arrived[-1][1]
                self._pending[node_id] = [
                    p for p in self._pending[node_id] if p[0] > t + eps
                ]

    def observe(self, t: float) -> dict[str, NodeState]:
        return dict(self._visible)
