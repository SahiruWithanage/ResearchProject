"""Controller: snapshots node states, asks the allocator, records the outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext
from src.models import AllocationOutcome, EdgeNode, Task
from src.simulation.estimates import CompletionEstimator

if TYPE_CHECKING:
    from src.simulation.processing import NodeRuntime


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
    ) -> None:
        if not managed_nodes:
            raise ValueError(f"controller {id!r} has no managed nodes")
        self.id = id
        self.allocator = allocator
        self.allocator_type = allocator_type
        self.managed_nodes = managed_nodes
        self.parent_id = parent_id
        self._runtime_by_node: dict[str, NodeRuntime] = {
            rt.node_id: rt for rt in managed_nodes
        }
        self._candidates: list[EdgeNode] = [rt.node for rt in managed_nodes]
        self.outcomes: dict[str, AllocationOutcome] = {}

    def submit(
        self,
        task: Task,
        t: float,
        estimator: CompletionEstimator,
    ) -> AllocationOutcome:
        """Run the allocator and record the decision (no enqueue)."""
        states = {rt.node_id: rt.snapshot(t) for rt in self.managed_nodes}
        context = DecisionContext(
            task=task,
            candidates=self._candidates,
            states=states,
            t=t,
            estimator=estimator,
        )
        chosen_id = self.allocator.allocate(context)

        if chosen_id not in self._runtime_by_node:
            raise RuntimeError(
                f"allocator returned unknown node_id {chosen_id!r} for "
                f"task {task.task_id!r}; controller {self.id!r} manages "
                f"{sorted(self._runtime_by_node)}"
            )

        source_id = task.source_node_id or chosen_id
        eta = estimator.estimated_completion(
            source_id,
            self._runtime_by_node[chosen_id].node,
            task,
            states,
            t,
        )
        outcome = AllocationOutcome(
            task_id=task.task_id,
            decision_time=t,
            allocator_type=self.allocator_type,
            selected_node=chosen_id,
            estimated_completion_time=eta,
            transfer_start=t,
        )
        self.outcomes[task.task_id] = outcome
        return outcome

    def record_completion(self, task: Task, completion_time: float) -> None:
        outcome = self.outcomes.get(task.task_id)
        if outcome is None:
            raise KeyError(
                f"task {task.task_id!r} is not tracked by controller "
                f"{self.id!r}"
            )
        outcome.actual_completion_time = completion_time
        outcome.deadline_met = completion_time <= task.deadline

    def has_task(self, task_id: str) -> bool:
        return task_id in self.outcomes

    @property
    def managed_node_ids(self) -> list[str]:
        return list(self._runtime_by_node)
