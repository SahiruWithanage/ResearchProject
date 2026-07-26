"""Aggregate a run's outcomes into the summary numbers the UI shows.

Same definitions as tools/evidence_runs.py and the CLI summary: latency is
decision -> return (or completion when there is no return leg), deadline %
is over completed tasks, placement counts every allocated task.
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
    done = [o for o in outcomes if o.actual_completion_time is not None]
    met = [o for o in done if o.deadline_met]
    lost = [o for o in outcomes if o.task_lost]
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
        "tasks_generated": len(outcomes),
        "tasks_completed": len(done),
        "tasks_lost": len(lost),
        "deadline_met": len(met),
        "deadline_pct": 100.0 * len(met) / len(done) if done else 0.0,
        "mean_latency_s": statistics.mean(latency) if latency else 0.0,
        "placement": {k: placement[k] for k in sorted(placement)},
        "max_queue": {k: max_queue[k] for k in sorted(max_queue)},
    }
