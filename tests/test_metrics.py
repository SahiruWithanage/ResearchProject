"""Aggregate metrics beyond the headline success rate.

Two allocators can score identically and behave completely differently.
These are the numbers that tell them apart: where the work went, how evenly
it was spread, how hard the misses missed, and what the tail looked like.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from src.config import parse_config
from src.models import AllocationOutcome, NodeState
from src.simulation.environment import Environment, EnvironmentResult
from ui.metrics import _jains_index, _percentile, summarize


# ---------------------------------------------------------------------------
# The building blocks
# ---------------------------------------------------------------------------


def test_percentile_picks_the_nearest_rank() -> None:
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert _percentile(values, 50) == 50.0
    assert _percentile(values, 95) == 95.0
    assert _percentile(values, 100) == 100.0
    assert _percentile([], 95) == 0.0
    assert _percentile([7.0], 95) == 7.0


def test_jains_index_endpoints() -> None:
    # perfectly even
    assert _jains_index([5, 5, 5, 5]) == 1.0
    # everything on one node of four -> 1/n
    assert _jains_index([20, 0, 0, 0]) == pytest.approx(0.25)
    # nothing happened at all
    assert _jains_index([0, 0]) == 0.0
    assert _jains_index([]) == 0.0


def test_jains_index_counts_idle_nodes() -> None:
    """An ignored node must drag fairness down, not be quietly excluded."""
    used_only = _jains_index([10, 10])
    with_idle = _jains_index([10, 10, 0])
    assert used_only == 1.0
    assert with_idle < used_only


# ---------------------------------------------------------------------------
# Lateness magnitude
# ---------------------------------------------------------------------------


def _outcome(**kw) -> AllocationOutcome:
    base = dict(
        task_id="t",
        decision_time=0.0,
        allocator_type="x",
        selected_node="n1",
        estimated_completion_time=1.0,
        source_node_id="n1",
    )
    base.update(kw)
    return AllocationOutcome(**base)


def test_lateness_separates_a_near_miss_from_a_disaster() -> None:
    outcomes = [
        _outcome(task_id="a", deadline=10.0, actual_completion_time=10.1,
                 deadline_met=False),
        _outcome(task_id="b", deadline=10.0, actual_completion_time=25.0,
                 deadline_met=False),
        _outcome(task_id="c", deadline=10.0, actual_completion_time=5.0,
                 deadline_met=True),
    ]
    s = summarize(EnvironmentResult(outcomes=outcomes, snapshots=[], final_time=30.0))
    assert s["tasks_late"] == 2
    # 0.1 and 15.0 late
    assert s["mean_lateness_s"] == pytest.approx(7.55)
    assert s["max_lateness_s"] == pytest.approx(15.0)


def test_no_misses_means_no_lateness() -> None:
    outcomes = [
        _outcome(task_id="a", deadline=10.0, actual_completion_time=5.0,
                 deadline_met=True)
    ]
    s = summarize(EnvironmentResult(outcomes=outcomes, snapshots=[], final_time=10.0))
    assert s["mean_lateness_s"] == 0.0
    assert s["max_lateness_s"] == 0.0


# ---------------------------------------------------------------------------
# Offload ratio
# ---------------------------------------------------------------------------


def test_offload_ratio_counts_work_that_left_home() -> None:
    outcomes = [
        _outcome(task_id="a", source_node_id="n1", selected_node="n1"),
        _outcome(task_id="b", source_node_id="n1", selected_node="n2"),
        _outcome(task_id="c", source_node_id="n1", selected_node="n2"),
        # dropped tasks were never placed, so they are not in the ratio
        _outcome(task_id="d", source_node_id="n1", selected_node=None,
                 task_lost=True, deadline_met=False),
    ]
    s = summarize(EnvironmentResult(outcomes=outcomes, snapshots=[], final_time=10.0))
    assert s["offload_ratio"] == pytest.approx(200.0 / 3)


# ---------------------------------------------------------------------------
# Per-node averages
# ---------------------------------------------------------------------------


def test_node_averages_are_over_the_whole_run() -> None:
    snaps = [
        NodeState(time_step=0.0, node_id="n1", queue_length=0, active_tasks=0,
                  cpu_utilisation=0.0, memory_utilisation=0.0),
        NodeState(time_step=1.0, node_id="n1", queue_length=4, active_tasks=2,
                  cpu_utilisation=1.0, memory_utilisation=0.5),
    ]
    s = summarize(EnvironmentResult(outcomes=[], snapshots=snaps, final_time=2.0))
    assert s["avg_queue"]["n1"] == pytest.approx(2.0)
    assert s["max_queue"]["n1"] == 4
    assert s["avg_cpu_utilisation"]["n1"] == pytest.approx(0.5)
    assert s["avg_memory_utilisation"]["n1"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# End to end: the metrics that distinguish equal-scoring allocators
# ---------------------------------------------------------------------------


def test_equal_success_rates_can_hide_different_behaviour() -> None:
    """weighted_score and load_aware both score 96.1% on the demo config,
    but one concentrates work and the other spreads it. Without the
    secondary metrics they would look interchangeable."""
    raw = yaml.safe_load(open("configs/demo.yaml", encoding="utf-8").read())

    def run(alloc: str):
        r = deepcopy(raw)
        r["controllers"][0]["allocator"] = {"type": alloc}
        return summarize(Environment(parse_config(r)).run())

    ws = run("weighted_score")
    la = run("load_aware")

    assert ws["success_rate"] == pytest.approx(la["success_rate"], abs=0.1)
    # ...yet they differ clearly in how work was distributed
    assert la["load_fairness"] > ws["load_fairness"]
    assert ws["offload_ratio"] > la["offload_ratio"]


def test_every_summary_field_is_json_safe() -> None:
    import json

    raw = yaml.safe_load(open("configs/demo.yaml", encoding="utf-8").read())
    s = summarize(Environment(parse_config(raw)).run())
    json.dumps(s)  # must not raise
