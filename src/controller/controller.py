"""Controller: snapshots node states, asks the allocator, records the outcome."""

from __future__ import annotations

import numpy as np

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.controller.observability import ObservabilityModel, PerfectObservability
from src.models import AllocationOutcome, EdgeNode, NodeState, Task
from src.simulation.estimates import CompletionEstimator

if TYPE_CHECKING:
    from src.simulation.processing import NodeRuntime


@dataclass(frozen=True, slots=True)
class _InFlight:
    """One dispatch the controller remembers until the node reports back.

    ``expected_arrival`` is when the controller believes the node will have
    the task: dispatch time plus its own estimate of the transfer. A report
    taken at or after that moment supersedes this record.
    """

    node_id: str
    cpu_demand: float
    expected_arrival: float


class Controller:
    """Owns a set of nodes. Allocates tasks via the configured strategy.

    Does **not** enqueue tasks; :class:`~src.simulation.environment.Environment`
    dispatches after allocation (local enqueue or remote transit).
    """

    def __init__(
        self,
        *,
        id: str,
        allocator: Allocator,
        allocator_type: str,
        managed_nodes: list[NodeRuntime],
        parent_id: str | None = None,
        observability: ObservabilityModel | None = None,
        scheduling_delay: float = 0.0,
        host: str | None = None,
        rng: np.random.Generator | None = None,
    ) -> None:
        if not managed_nodes:
            raise ValueError(f"controller {id!r} has no managed nodes")
        if scheduling_delay < 0:
            raise ValueError(
                f"scheduling_delay must be >= 0, got {scheduling_delay}"
            )
        self.id = id
        self.allocator = allocator
        self.allocator_type = allocator_type
        self.managed_nodes = managed_nodes
        self.parent_id = parent_id
        # Node this controller physically runs on, or None if it is placeless.
        self.host = host
        # Dedicated stream for allocators that need randomness. Separate
        # from the generators and the network, so drawing from it cannot
        # shift arrivals or jitter and break the identical-world guarantee.
        self.rng = rng
        self.observability = observability or PerfectObservability()
        self.observability.attach(managed_nodes)
        self.scheduling_delay = float(scheduling_delay)
        self._runtime_by_node: dict[str, NodeRuntime] = {
            rt.node_id: rt for rt in managed_nodes
        }
        self.outcomes: dict[str, AllocationOutcome] = {}
        # What this controller has sent that the nodes' reports cannot yet
        # reflect. See _adjusted_states.
        self._in_flight: list[_InFlight] = []

    def _adjusted_states(
        self, states: Mapping[str, NodeState]
    ) -> dict[str, NodeState]:
        """Observed reports, plus work this controller knows it has sent.

        A controller is stale about *node state*, but it is not amnesiac
        about its own actions. Without this, tasks decided together all see
        the same empty node and stampede onto it: the first placement is
        invisible to the second because the task is still crossing the
        network, or because the next heartbeat has not arrived.

        An entry is counted only while the node's latest report is older
        than the moment we expect that node to have *received* the task.
        Once a newer report exists it is authoritative - it already counts
        the task if it is still queued there, and already excludes it if it
        finished - so the memory defers to it and drops the entry. That
        makes the memory self-clearing and bounded by the report interval,
        with no completion channel required (which would hand the
        controller instant knowledge and quietly erase the very staleness
        under study).
        """
        if not self._in_flight:
            return dict(states)

        pending_tasks: dict[str, int] = {}
        pending_work: dict[str, float] = {}
        keep: list[_InFlight] = []
        for entry in self._in_flight:
            state = states.get(entry.node_id)
            # Superseded: the node has reported since it should have had this.
            if state is not None and state.time_step >= entry.expected_arrival:
                continue
            keep.append(entry)
            pending_tasks[entry.node_id] = pending_tasks.get(entry.node_id, 0) + 1
            pending_work[entry.node_id] = (
                pending_work.get(entry.node_id, 0.0) + entry.cpu_demand
            )
        self._in_flight = keep

        if not pending_tasks:
            return dict(states)

        adjusted = {}
        for node_id, state in states.items():
            extra_tasks = pending_tasks.get(node_id, 0)
            if not extra_tasks:
                adjusted[node_id] = state
                continue
            adjusted[node_id] = replace(
                state,
                queue_length=state.queue_length + extra_tasks,
                queued_work=state.queued_work + pending_work[node_id],
            )
        return adjusted

    def submit(
        self,
        task: Task,
        t: float,
        estimator: CompletionEstimator,
    ) -> AllocationOutcome:
        """Run the allocator and record the decision (no enqueue).

        Allocators only ever see *eligible* candidates: nodes that are
        suitable for the task (type / memory / GPU) and currently have
        room (queue limit). If no node is eligible - including the task's
        own source - the task is recorded as lost. A task is therefore
        never dropped while its source still has room, because a
        with-room source is itself an eligible candidate.

        A node the source cannot reach over the network is not a candidate
        either. The source itself is always reachable from itself - running
        a task locally needs no network.

        The *states* the allocator scores with come from the observability
        model (possibly stale heartbeat reports); eligibility (limits,
        suitability, reachability) stays physical - the node itself knows if
        it's full, and the topology knows what it can talk to.
        """
        states = self._adjusted_states(self.observability.observe(t))
        eligible: list[EdgeNode] = [
            rt.node
            for rt in self.managed_nodes
            if rt.is_available()
            and rt.node.is_suitable(task)
            and rt.has_room()
            and self._reachable(task, rt.node.node_id, estimator)
        ]

        if not eligible:
            outcome = AllocationOutcome(
                task_id=task.task_id,
                decision_time=t,
                allocator_type=self.allocator_type,
                selected_node=None,
                estimated_completion_time=None,
                arrival_time=task.arrival_time,
                source_node_id=task.source_node_id,
                deadline=task.deadline,
                task_lost=True,
                # A dropped task definitively missed its deadline. Leaving
                # this None would make it indistinguishable from a task that
                # was still in flight when the run ended.
                deadline_met=False,
            )
            self.outcomes[task.task_id] = outcome
            return outcome

        context = DecisionContext(
            task=task,
            candidates=eligible,
            states=states,
            t=t,
            estimator=estimator,
            rng=self.rng,
        )
        chosen_id = self.allocator.allocate(context)

        if chosen_id not in self._runtime_by_node:
            raise RuntimeError(
                f"allocator returned unknown node_id {chosen_id!r} for "
                f"task {task.task_id!r}; controller {self.id!r} manages "
                f"{sorted(self._runtime_by_node)}"
            )
        if all(node.node_id != chosen_id for node in eligible):
            raise RuntimeError(
                f"allocator returned ineligible node_id {chosen_id!r} for "
                f"task {task.task_id!r} (full or unsuitable); eligible: "
                f"{sorted(n.node_id for n in eligible)}"
            )

        source_id = task.source_node_id or chosen_id
        eta = (
            estimator.estimated_completion(
                source_id,
                self._runtime_by_node[chosen_id].node,
                task,
                states,
                t,
            )
            + self.scheduling_delay
        )
        outcome = AllocationOutcome(
            task_id=task.task_id,
            decision_time=t,
            allocator_type=self.allocator_type,
            selected_node=chosen_id,
            estimated_completion_time=eta,
            arrival_time=task.arrival_time,
            source_node_id=task.source_node_id,
            deadline=task.deadline,
            # The payload leaves once the scheduling/orchestration overhead
            # has elapsed; the Environment dispatches from this instant.
            transfer_start=t + self.scheduling_delay,
        )
        self.outcomes[task.task_id] = outcome

        # Remember it until the chosen node has reported since receiving it.
        # The expected arrival is the controller's own estimate - the only
        # thing it legitimately knows - so a task still crossing the network
        # keeps counting, which is correct: it is real work heading there
        # and the destination has not seen it either.
        transfer = (
            estimator.uplink_delay(source_id, chosen_id, task, t)
            if chosen_id != source_id
            else 0.0
        )
        self._in_flight.append(
            _InFlight(
                node_id=chosen_id,
                cpu_demand=task.cpu_demand,
                expected_arrival=t + self.scheduling_delay + transfer,
            )
        )
        return outcome

    def record_completion(
        self,
        task: Task,
        completion_time: float,
        *,
        awaiting_return: bool = False,
    ) -> None:
        """Record compute completion.

        With ``awaiting_return=True`` the deadline verdict is deferred until
        :meth:`record_return` - the requester only has the result once the
        downlink delivers it.
        """
        outcome = self.outcomes.get(task.task_id)
        if outcome is None:
            raise KeyError(
                f"task {task.task_id!r} is not tracked by controller "
                f"{self.id!r}"
            )
        outcome.actual_completion_time = completion_time
        if not awaiting_return:
            outcome.deadline_met = completion_time <= task.deadline

    def record_return(self, task: Task, return_time: float) -> None:
        """Record the result arriving back at the source; judge the deadline."""
        outcome = self.outcomes.get(task.task_id)
        if outcome is None:
            raise KeyError(
                f"task {task.task_id!r} is not tracked by controller "
                f"{self.id!r}"
            )
        outcome.return_end = return_time
        outcome.deadline_met = return_time <= task.deadline

    @staticmethod
    def _reachable(task: Task, node_id: str, estimator: CompletionEstimator) -> bool:
        """Can this task's payload actually get to that node?

        Local execution never touches the network, so a node is always
        reachable from itself.
        """
        if node_id == task.source_node_id:
            return True
        return estimator.network.can_reach(task.source_node_id, node_id)

    def record_loss(self, task: Task) -> None:
        """Mark an already-allocated task as lost (node crash, dead arrival)."""
        outcome = self.outcomes.get(task.task_id)
        if outcome is None:
            raise KeyError(
                f"task {task.task_id!r} is not tracked by controller "
                f"{self.id!r}"
            )
        outcome.task_lost = True
        outcome.deadline_met = False

    def has_task(self, task_id: str) -> bool:
        return task_id in self.outcomes

    @property
    def managed_node_ids(self) -> list[str]:
        return list(self._runtime_by_node)
