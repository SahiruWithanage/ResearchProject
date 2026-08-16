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
    latency = sorted(
        (o.return_end or o.actual_completion_time) - o.decision_time for o in done
    )
    placement = Counter(o.selected_node for o in outcomes if o.selected_node)

    # --- per-node behaviour, averaged over the run --------------------------
    max_queue: dict[str, int] = {}
    queue_sum: dict[str, float] = {}
    cpu_sum: dict[str, float] = {}
    mem_sum: dict[str, float] = {}
    counts: dict[str, int] = {}
    for s in result.snapshots:
        if s.queue_length > max_queue.get(s.node_id, -1):
            max_queue[s.node_id] = s.queue_length
        queue_sum[s.node_id] = queue_sum.get(s.node_id, 0.0) + s.queue_length
        cpu_sum[s.node_id] = cpu_sum.get(s.node_id, 0.0) + s.cpu_utilisation
        mem_sum[s.node_id] = mem_sum.get(s.node_id, 0.0) + s.memory_utilisation
        counts[s.node_id] = counts.get(s.node_id, 0) + 1

    node_ids = sorted(counts)
    avg_queue = {n: queue_sum[n] / counts[n] for n in node_ids}
    cpu_util = {n: cpu_sum[n] / counts[n] for n in node_ids}
    mem_util = {n: mem_sum[n] / counts[n] for n in node_ids}

    # --- how late were the misses? ------------------------------------------
    # A task 0.1 s late is not the failure a task 10 s late is, and a bare
    # count of misses cannot tell them apart.
    lateness = sorted(
        (o.return_end or o.actual_completion_time) - o.deadline
        for o in late
        if o.deadline is not None and o.actual_completion_time is not None
    )

    # --- how much work left home? -------------------------------------------
    placed = [o for o in outcomes if o.selected_node and o.source_node_id]
    offloaded = [o for o in placed if o.selected_node != o.source_node_id]

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
        # The tail is what breaks deadlines; a mean can look healthy while a
        # slow minority misses consistently.
        "p95_latency_s": _percentile(latency, 95),
        # --- lateness magnitude ---
        "mean_lateness_s": statistics.mean(lateness) if lateness else 0.0,
        "max_lateness_s": lateness[-1] if lateness else 0.0,
        # --- where work went ---
        "placement": {k: placement[k] for k in sorted(placement)},
        "offload_ratio": (100.0 * len(offloaded) / len(placed)) if placed else 0.0,
        # --- node behaviour over the run ---
        "max_queue": {k: max_queue[k] for k in node_ids},
        "avg_queue": {k: round(avg_queue[k], 4) for k in node_ids},
        "avg_cpu_utilisation": {k: round(cpu_util[k], 4) for k in node_ids},
        "avg_memory_utilisation": {k: round(mem_util[k], 4) for k in node_ids},
        # 1.0 = every node carried an equal share, 1/n = one node carried
        # everything. Idle nodes are included, or ignoring a node entirely
        # would score as perfectly fair.
        "load_fairness": _jains_index(
            [placement.get(n, 0) for n in node_ids]
        ),
    }


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round(pct / 100.0 * len(sorted_values))) - 1))
    return sorted_values[k]


def _jains_index(loads: list[float]) -> float:
    """Jain's fairness index: how evenly work was spread.

    ``(sum x)^2 / (n * sum x^2)``, the standard measure in this literature.
    Returns 1.0 when every node carried the same amount and 1/n when one
    node carried all of it, so it is comparable across topologies of
    different size in a way that a raw spread is not.
    """
    n = len(loads)
    total = sum(loads)
    if n == 0 or total <= 0:
        return 0.0
    return round((total * total) / (n * sum(x * x for x in loads)), 4)
