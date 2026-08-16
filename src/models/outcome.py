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
    # Which node asked for this work. Recorded rather than parsed back out
    # of the task id, which only worked because ids happen to be built as
    # "<source>_<counter>".
    source_node_id: str | None = None
    # Absolute time this task was due. Without it the log cannot show *how
    # late* a miss was, and deadline_met cannot be checked from the CSV.
    deadline: float | None = None
    # When the generator emitted the task. The controller decides at the next
    # tick boundary, so decision_time - arrival_time is the wait for a
    # decision (< dt). Deadlines are measured from arrival, not decision.
    arrival_time: float | None = None
    transfer_start: float | None = None
    transfer_end: float | None = None
    compute_start: float | None = None
    actual_completion_time: float | None = None
    # When the result payload arrived back at the source (downlink). None
    # while in flight, and for local runs / tasks with result_size == 0.
    return_end: float | None = None
    deadline_met: bool | None = None
    # True when no eligible node (suitable + has room) existed at decision
    # time, including the task's own source: the task was dropped.
    task_lost: bool = False
