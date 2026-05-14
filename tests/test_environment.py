"""End-to-end tests for the Environment orchestrator."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.config import parse_config
from src.simulation import Environment, EnvironmentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_raw() -> dict[str, Any]:
    # cpu_demand=3 on a 1-worker source with 1 task/sec arrivals overloads it, forcing offload.
    return {
        "seed": 42,
        "sim_duration": 10.0,
        "dt": 1.0,
        "controllers": [
            {
                "id": "ctrl_main",
                "allocator": {
                    "type": "local_first_helper_offload",
                    "max_local_queue": 2,
                },
                "manages": ["node_1", "node_h"],
                "parent": None,
            }
        ],
        "nodes": [
            {
                "id": "node_1",
                "type": "source",
                "cpu_capacity": 1.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {
                    "generator": {
                        "type": "fixed_interval",
                        "interval": 1.0,
                        "cpu_demand": 3.0,
                        "deadline_offset": 100.0,
                    }
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 1.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {
            "output_dir": "logs/test_env",
            "log_state_every": 1.0,
        },
    }


@pytest.fixture
def poisson_raw() -> dict[str, Any]:
    return {
        "seed": 42,
        "sim_duration": 100.0,
        "dt": 1.0,
        "controllers": [
            {
                "id": "ctrl_main",
                "allocator": {"type": "local_first_helper_offload"},
                "manages": ["node_1", "node_2", "node_h"],
                "parent": None,
            }
        ],
        "nodes": [
            {
                "id": "node_1",
                "type": "source",
                "cpu_capacity": 2.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {
                    "generator": {"type": "poisson", "rate": 0.3},
                },
            },
            {
                "id": "node_2",
                "type": "source",
                "cpu_capacity": 2.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {
                    "generator": {"type": "poisson", "rate": 0.3},
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 2.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {
            "output_dir": "logs/test_env",
            "log_state_every": 1.0,
        },
    }


# ===========================================================================
# Construction wiring
# ===========================================================================

def test_environment_builds_nodes_runtimes_generators_and_controllers(base_raw):
    env = Environment(parse_config(base_raw))
    assert set(env.nodes) == {"node_1", "node_h"}
    assert set(env.runtimes) == {"node_1", "node_h"}
    assert set(env.generators) == {"node_1"}  # only sources get generators
    assert set(env.controllers) == {"ctrl_main"}


def test_environment_controller_manages_the_configured_nodes(base_raw):
    env = Environment(parse_config(base_raw))
    ctrl = env.controllers["ctrl_main"]
    assert sorted(ctrl.managed_node_ids) == ["node_1", "node_h"]


def test_environment_unknown_allocator_type_raises(base_raw):
    base_raw["controllers"][0]["allocator"]["type"] = "nonexistent"
    with pytest.raises(KeyError, match="unknown allocator 'nonexistent'"):
        Environment(parse_config(base_raw))


def test_environment_unknown_generator_type_raises(base_raw):
    base_raw["nodes"][0]["source"]["generator"]["type"] = "nonexistent"
    with pytest.raises(KeyError, match="unknown generator 'nonexistent'"):
        Environment(parse_config(base_raw))


# ===========================================================================
# Deterministic fixed-interval run: predictable counts and timings
# ===========================================================================

def test_fixed_interval_run_produces_expected_task_count(base_raw):
    # interval=1.0 over [0, 10) -> 10 tasks generated.
    env = Environment(parse_config(base_raw))
    result = env.run()
    assert isinstance(result, EnvironmentResult)
    assert len(result.outcomes) == 10
    assert result.final_time == 10.0


def test_fixed_interval_run_keeps_first_then_offloads(base_raw):
    env = Environment(parse_config(base_raw))
    result = env.run()

    # Order outcomes by decision_time (task ID is enough as tie-break).
    outcomes = sorted(result.outcomes, key=lambda o: (o.decision_time, o.task_id))
    # At least one task should end up on the helper after the source saturates.
    on_source = [o for o in outcomes if o.selected_node == "node_1"]
    on_helper = [o for o in outcomes if o.selected_node == "node_h"]
    assert len(on_source) >= 1
    assert len(on_helper) >= 1


def test_fixed_interval_some_tasks_actually_complete(base_raw):
    env = Environment(parse_config(base_raw))
    result = env.run()
    completed = [o for o in result.outcomes if o.actual_completion_time is not None]
    assert len(completed) >= 1
    # Generous 100s deadline_offset: every completed task should meet its deadline.
    for o in completed:
        assert o.deadline_met is True


# ===========================================================================
# Snapshots: rising and draining queues
# ===========================================================================

def test_snapshots_include_initial_state(base_raw):
    env = Environment(parse_config(base_raw))
    result = env.run()
    initial = [s for s in result.snapshots if s.time_step == 0.0]
    # One snapshot per node at t=0.
    assert {s.node_id for s in initial} == {"node_1", "node_h"}
    assert all(s.queue_length == 0 for s in initial)


def test_snapshots_taken_at_log_state_every_intervals(base_raw):
    env = Environment(parse_config(base_raw))
    result = env.run()
    distinct_times = sorted({s.time_step for s in result.snapshots})
    # Snapshots at t=0, 1, 2, ..., 10 (the run ends at sim_duration=10).
    assert distinct_times == [float(i) for i in range(11)]


def test_queue_grows_when_source_saturated(base_raw):
    env = Environment(parse_config(base_raw))
    result = env.run()
    by_time = {(s.time_step, s.node_id): s for s in result.snapshots}
    early = by_time.get((1.0, "node_1"))
    later = by_time.get((5.0, "node_1"))
    assert early is not None and later is not None
    # The source's queue must exceed its initial value at some point in the run.
    max_queue = max(
        s.queue_length for s in result.snapshots if s.node_id == "node_1"
    )
    assert max_queue >= 2


# ===========================================================================
# Reproducibility
# ===========================================================================

def test_same_seed_produces_identical_outcomes(poisson_raw):
    a = Environment(parse_config(copy.deepcopy(poisson_raw))).run()
    b = Environment(parse_config(copy.deepcopy(poisson_raw))).run()
    assert len(a.outcomes) == len(b.outcomes)
    for x, y in zip(a.outcomes, b.outcomes):
        assert x.task_id == y.task_id
        assert x.decision_time == y.decision_time
        assert x.selected_node == y.selected_node
        assert x.actual_completion_time == y.actual_completion_time
        assert x.deadline_met == y.deadline_met


def test_same_seed_produces_identical_snapshots(poisson_raw):
    a = Environment(parse_config(copy.deepcopy(poisson_raw))).run()
    b = Environment(parse_config(copy.deepcopy(poisson_raw))).run()
    assert len(a.snapshots) == len(b.snapshots)
    for x, y in zip(a.snapshots, b.snapshots):
        assert x.time_step == y.time_step
        assert x.node_id == y.node_id
        assert x.queue_length == y.queue_length
        assert x.active_tasks == y.active_tasks


def test_different_seed_produces_different_outcomes(poisson_raw):
    poisson_raw["seed"] = 42
    a = Environment(parse_config(copy.deepcopy(poisson_raw))).run()
    poisson_raw["seed"] = 43
    b = Environment(parse_config(copy.deepcopy(poisson_raw))).run()
    a_ids = sorted(o.task_id for o in a.outcomes)
    b_ids = sorted(o.task_id for o in b.outcomes)
    assert a_ids != b_ids


# ===========================================================================
# Sandbox flexibility
# ===========================================================================

def test_adding_more_nodes_works_without_code_change(poisson_raw):
    poisson_raw["nodes"].append(
        {
            "id": "node_3",
            "type": "source",
            "cpu_capacity": 2.0,
            "memory_capacity": 8.0,
            "tier": "edge",
            "source": {"generator": {"type": "poisson", "rate": 0.1}},
        }
    )
    poisson_raw["nodes"].append(
        {
            "id": "node_h2",
            "type": "helper",
            "cpu_capacity": 4.0,
            "memory_capacity": 8.0,
            "tier": "edge",
        }
    )
    poisson_raw["controllers"][0]["manages"] += ["node_3", "node_h2"]

    env = Environment(parse_config(poisson_raw))
    result = env.run()
    assert set(env.nodes) == {"node_1", "node_2", "node_3", "node_h", "node_h2"}
    assert set(env.generators) == {"node_1", "node_2", "node_3"}
    assert len(result.outcomes) > 0


def test_run_handles_empty_workload(base_raw):
    # Zero-rate Poisson: pipeline still works, snapshots still produced.
    base_raw["nodes"][0]["source"]["generator"] = {
        "type": "poisson",
        "rate": 0.0,
    }
    env = Environment(parse_config(base_raw))
    result = env.run()
    assert result.outcomes == []
    assert len(result.snapshots) > 0
    assert result.final_time == 10.0


def test_sim_duration_not_multiple_of_dt_handled(base_raw):
    base_raw["sim_duration"] = 5.5
    base_raw["dt"] = 1.0
    env = Environment(parse_config(base_raw))
    result = env.run()
    assert result.final_time == 5.5
