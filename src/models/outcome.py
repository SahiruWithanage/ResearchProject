"""AllocationOutcome: per-task record of where it went and when it finished.

Mutable because completion fields are filled in after the decision is made.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(slots=True)
class AllocationOutcome:
    task_id: str
    decision_time: float
    allocator_type: str
    selected_node: str | None
    estimated_completion_time: float | None
    transfer_start: float | None = None
    transfer_end: float | None = None
    compute_start: float | None = None
    actual_completion_time: float | None = None
    deadline_met: bool | None = None
    # True when no eligible node (suitable + has room) existed at decision
    # time, including the task's own source: the task was dropped.
    task_lost: bool = False
