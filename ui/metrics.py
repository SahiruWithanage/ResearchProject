"""Aggregate a run's outcomes into the summary numbers the UI shows.

The headline number is **success rate over every task generated**. A task
counts as a success only when it ran to completion, its result reached the
source that asked for it (when there is a result to return), and all of
that happened before its deadline. Anything else is a failure: dropped
because nowhere could take it, destroyed by a node crash, delivered to a
node or requester that had gone down, finished late, or never finished.

Dividing by *completed* tasks instead - as this used to - hides dropped
tasks and rewards an allocator that sheds load under pressure, which is
exactly backwards.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from src.simulation.environment import EnvironmentResult


def summarize(
    result: EnvironmentResult, wall_seconds: float | None = None
) -> dict[str, Any]:
    outcomes = result.outcomes
    generated = len(outcomes)

    # deadline_met is True only after a task completed, returned its result
    # if one was due, and beat its deadline - so it already encodes the
    # whole success condition.
    succeeded = [o for o in outcomes if o.deadline_met is True]
    lost = [o for o in outcomes if o.task_lost]
    late = [o for o in outcomes if o.deadline_met is False and not o.task_lost]
    # Still in flight when the clock stopped: no verdict was ever reached.
    unfinished = [o for o in outcomes if o.deadline_met is None and not o.task_lost]

    done = [o for o in outcomes if o.actual_completion_time is not None]
    latency = [
        (o.return_end or o.actual_completion_time) - o.decision_time for o in done
    ]
    placement = Counter(o.selected_node for o in outcomes if o.selected_node)
    max_queue: dict[str, int] = {}
    for s in result.snapshots:
        if s.queue_length > max_queue.get(s.node_id, -1):
            max_queue[s.node_id] = s.queue_length

    return {
        "final_time": result.final_time,
        "wall_seconds": wall_seconds,
        "tasks_generated": generated,
        # --- the headline ---
        "tasks_succeeded": len(succeeded),
        "success_rate": 100.0 * len(succeeded) / generated if generated else 0.0,
        # --- how the rest failed ---
        "tasks_lost": len(lost),
        "tasks_late": len(late),
        "tasks_unfinished": len(unfinished),
        # --- secondary diagnostics ---
        "tasks_completed": len(done),
        # Kept because it is the conventional figure, but named so it cannot
        # be mistaken for the headline: it ignores dropped tasks.
        "deadline_pct_of_completed": (
            100.0 * len([o for o in done if o.deadline_met]) / len(done)
            if done
            else 0.0
        ),
        "mean_latency_s": statistics.mean(latency) if latency else 0.0,
        "median_latency_s": statistics.median(latency) if latency else 0.0,
        "placement": {k: placement[k] for k in sorted(placement)},
        "max_queue": {k: max_queue[k] for k in sorted(max_queue)},
    }
