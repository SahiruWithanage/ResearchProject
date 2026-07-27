"""Instability scenarios (Stage 8): scripted changes to the world over time.

A scenario is a pluggable component that pokes the environment while the
simulation runs - nodes crash and come back, reliability drifts. Configured
as a top-level YAML list; every entry is one scenario instance:

.. code-block:: yaml

    scenarios:
      - type: node_failure
        node: node_h
        fail_at: 100.0
        recover_at: 130.0
        recovery_duration: 20.0      # optional ramp at reduced speed
        recovery_speed_factor: 0.5   # speed multiplier during the ramp
      - type: reliability_decay
        node: node_2
        start: 50.0
        end: 200.0
        to_value: 0.3                # linearly from from_value (1.0) to this

Contract: ``tick(t, world)`` is called once per simulation tick at the
tick's *start*, before any processing. ``world`` is the Environment,
offering exactly four hooks (see ``Environment``): ``fail_node``,
``begin_node_recovery``, ``restore_node``, ``set_node_reliability``.
Scenarios must be idempotent per phase - ``tick`` fires every tick.

What failure means (see DESIGN.md decisions 54-55): a failed node evicts
every held task (recorded as lost), does no work, stops sending
heartbeats, refuses in-flight arrivals (also lost), and is excluded from
allocation. Recovery may run at reduced speed for a while.

Already-covered instability axes that need no Scenario class: workload
bursts (``piecewise`` rate pattern), latency variation
(``varying_fluid_link``), partial observability (``heartbeat``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.config.factory import scenarios

if TYPE_CHECKING:
    from src.simulation.environment import Environment


class Scenario(ABC):
    """One scripted instability. ``tick`` runs at the start of every tick."""

    @abstractmethod
    def tick(self, t: float, world: Environment) -> None: ...


@scenarios.register("node_failure")
class NodeFailureScenario(Scenario):
    """One failure/recovery episode for one node.

    Timeline: healthy → **failed** at ``fail_at`` → **recovering** at
    ``recover_at`` (at ``recovery_speed_factor`` × speed for
    ``recovery_duration`` seconds) → healthy.
    """

    def __init__(
        self,
        *,
        node: str,
        fail_at: float,
        recover_at: float | None = None,
        recovery_duration: float = 0.0,
        recovery_speed_factor: float = 0.5,
    ) -> None:
        if fail_at < 0:
            raise ValueError(f"fail_at must be >= 0, got {fail_at}")
        if recover_at is not None and recover_at <= fail_at:
            raise ValueError(
                f"recover_at must be > fail_at, got {recover_at} <= {fail_at}"
            )
        if recovery_duration < 0:
            raise ValueError(
                f"recovery_duration must be >= 0, got {recovery_duration}"
            )
        if not 0 < recovery_speed_factor <= 1.0:
            raise ValueError(
                f"recovery_speed_factor must be in (0, 1], got "
                f"{recovery_speed_factor}"
            )
        self.node = node
        self.fail_at = float(fail_at)
        self.recover_at = None if recover_at is None else float(recover_at)
        self.recovery_duration = float(recovery_duration)
        self.recovery_speed_factor = float(recovery_speed_factor)
        self._phase = "healthy"  # healthy -> failed -> recovering -> done

    def tick(self, t: float, world: Environment) -> None:
        eps = 1e-12
        if self._phase == "healthy" and t + eps >= self.fail_at:
            world.fail_node(self.node)
            self._phase = "failed"
        if (
            self._phase == "failed"
            and self.recover_at is not None
            and t + eps >= self.recover_at
        ):
            if self.recovery_duration > 0:
                world.begin_node_recovery(self.node, self.recovery_speed_factor)
                self._phase = "recovering"
            else:
                world.restore_node(self.node)
                self._phase = "done"
        if (
            self._phase == "recovering"
            and t + eps >= self.recover_at + self.recovery_duration
        ):
            world.restore_node(self.node)
            self._phase = "done"


@scenarios.register("reliability_decay")
class ReliabilityDecayScenario(Scenario):
    """Linearly drift one node's reliability_score from ``from_value`` to
    ``to_value`` between ``start`` and ``end``.

    Purely informational for the deterministic baselines (they ignore it);
    the ``reliability_threshold`` allocator and the Stage 9 Bayesian layer
    read it from the (possibly stale) observed states.
    """

    def __init__(
        self,
        *,
        node: str,
        start: float,
        end: float,
        to_value: float,
        from_value: float = 1.0,
    ) -> None:
        if end <= start:
            raise ValueError(f"end must be > start, got {end} <= {start}")
        for name, v in (("from_value", from_value), ("to_value", to_value)):
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {v}")
        self.node = node
        self.start = float(start)
        self.end = float(end)
        self.from_value = float(from_value)
        self.to_value = float(to_value)

    def tick(self, t: float, world: Environment) -> None:
        if t <= self.start:
            value = self.from_value
        elif t >= self.end:
            value = self.to_value
        else:
            frac = (t - self.start) / (self.end - self.start)
            value = self.from_value + frac * (self.to_value - self.from_value)
        world.set_node_reliability(self.node, value)
