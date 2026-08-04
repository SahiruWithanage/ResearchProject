"""NetworkModel: contract for uplink delay between nodes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Task


class NetworkModel(ABC):
    """One-way transmission delays between nodes.

    Uplink carries the task payload (``data_size`` bytes) from the source
    to the executor; downlink carries the result (``result_size`` bytes)
    from the executor back to the source. Links may be asymmetric - the
    two directions resolve their specs independently.

    The method pairs split *estimation* from *realization*:

    - The ``expected_*`` methods are what allocators (via the
      CompletionEstimator) may call as often as they like. They MUST be
      deterministic and MUST NOT consume randomness - otherwise the number
      of RNG draws would depend on which allocator is running, breaking
      same-seed comparability between allocators.
    - ``uplink_delay`` / ``downlink_delay`` are realized delays, each
      sampled once per actual transfer by the Environment. Stochastic
      models draw jitter here.
    """

    def can_reach(self, source_id: str, target_id: str) -> bool:
        """Can ``source_id`` send anything to ``target_id`` at all?

        Not a delay question: an unreachable pair is not "very slow", it is
        no route. The Controller drops unreachable nodes from a task's
        candidate list, exactly as it drops unsuitable or full ones, so an
        unreachable node is never chosen and the task is logged as lost if
        nothing else can take it. Modelling it as a huge delay instead would
        leave tasks stuck in transit forever, silently distorting metrics.

        Defaults to fully connected, so models that do not care are unaffected.
        """
        return True

    @abstractmethod
    def uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Return the realized uplink duration in simulated seconds (>= 0).

        May consume randomness; call exactly once per dispatched task.
        """

    @abstractmethod
    def expected_uplink_delay(
        self,
        source_id: str,
        target_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Return the expected uplink duration in simulated seconds (>= 0).

        Deterministic: repeated calls with the same arguments return the
        same value and never consume randomness.
        """

    @abstractmethod
    def downlink_delay(
        self,
        executor_id: str,
        source_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Realized duration for the result (``result_size`` bytes) to travel
        executor -> source. May consume randomness; call once per return leg.
        """

    @abstractmethod
    def expected_downlink_delay(
        self,
        executor_id: str,
        source_id: str,
        task: Task,
        t: float,
    ) -> float:
        """Expected (deterministic, RNG-free) executor -> source result delay."""
