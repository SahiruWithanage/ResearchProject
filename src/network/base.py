"""NetworkModel: contract for uplink delay between nodes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Task


class NetworkModel(ABC):
    """Estimates one-way uplink delay from a source node to an executor node.

    Downlink is deferred; see ``resources/DELAY_MODEL.md``.

    The two methods split *estimation* from *realization*:

    - :meth:`expected_uplink_delay` is what allocators (via the
      CompletionEstimator) may call as often as they like. It MUST be
      deterministic and MUST NOT consume randomness — otherwise the number
      of RNG draws would depend on which allocator is running, breaking
      same-seed comparability between allocators.
    - :meth:`uplink_delay` is the realized delay, sampled once per actual
      dispatch by the Environment. Stochastic models draw jitter here.
    """

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
