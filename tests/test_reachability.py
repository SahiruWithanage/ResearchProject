"""Links can be severed: a pair with no route is not a candidate at all.

Reachability is an *eligibility* rule, not a huge delay. A severed pair must
never be chosen, and a task with nowhere to go must be logged as lost rather
than sitting in transit forever.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np
import pytest

from src.config import parse_config
from src.config.loader import ConfigError
from src.models import Task
from src.network.fluid_link import FluidLinkNetworkModel
from src.simulation.environment import Environment


def _task(source: str = "a") -> Task:
    return Task(
        task_id="t1",
        source_node_id=source,
        arrival_time=0.0,
        deadline=100.0,
        data_size=1000,
        cpu_demand=1.0,
        memory_demand=1.0,
        task_type="compute",
        priority=1,
    )


BASE: dict[str, Any] = {
    "seed": 5,
    "sim_duration": 20.0,
    "dt": 0.1,
    "network": {"type": "fluid_link", "default_profile": "wifi"},
    "controllers": [
        {
            "id": "ctrl",
            "allocator": {"type": "load_aware"},
            "manages": ["src", "near", "far"],
            "parent": None,
        }
    ],
    "nodes": [
        {
            "id": "src",
            "type": "source",
            "cpu_capacity": 1.0,
            "memory_capacity": 8.0,
            "tier": "edge",
            "accepts_task_types": ["other"],  # cannot run its own work
            "source": {
                "generator": {
                    "type": "fixed_interval",
                    "interval": 1.0,
                    "task_type": "compute",
                    "cpu_demand": 0.5,
                    "deadline_offset": 50.0,
                }
            },
        },
        {
            "id": "near",
            "type": "helper",
            "cpu_capacity": 2.0,
            "memory_capacity": 8.0,
            "tier": "edge",
        },
        {
            "id": "far",
            "type": "helper",
            "cpu_capacity": 2.0,
            "memory_capacity": 8.0,
            "tier": "edge",
        },
    ],
    "logging": {"output_dir": "logs/reach_test", "log_state_every": 1.0},
}


# ---------------------------------------------------------------------------
# The network model
# ---------------------------------------------------------------------------


def test_pairs_are_reachable_by_default():
    net = FluidLinkNetworkModel(default_profile="lan", rng=np.random.default_rng(1))
    assert net.can_reach("a", "b") is True


def test_profile_none_severs_one_direction():
    net = FluidLinkNetworkModel(
        default_profile="lan",
        links=[{"from": "a", "to": "b", "profile": "none"}],
        rng=np.random.default_rng(1),
    )
    assert net.can_reach("a", "b") is False
    assert net.can_reach("b", "a") is True  # directions are independent


def test_reachable_false_also_severs():
    net = FluidLinkNetworkModel(
        default_profile="lan",
        links=[{"from": "a", "to": "b", "reachable": False}],
        rng=np.random.default_rng(1),
    )
    assert net.can_reach("a", "b") is False


def test_severed_link_does_not_break_delay_queries():
    """Nothing should ever ask, but it must not explode if it does."""
    net = FluidLinkNetworkModel(
        default_profile="lan",
        links=[{"from": "a", "to": "b", "profile": "none"}],
        rng=np.random.default_rng(1),
    )
    assert net.expected_uplink_delay("a", "b", _task(), 0.0) >= 0.0


# ---------------------------------------------------------------------------
# End to end through the simulator
# ---------------------------------------------------------------------------


def test_tasks_avoid_a_severed_helper():
    raw = deepcopy(BASE)
    raw["network"]["links"] = [{"from": "src", "to": "far", "profile": "none"}]
    result = Environment(parse_config(raw)).run()
    placements = {o.selected_node for o in result.outcomes if o.selected_node}
    assert placements == {"near"}, "work was sent over a severed link"
    assert not [o for o in result.outcomes if o.task_lost]


def test_task_is_lost_when_nothing_is_reachable():
    """The source cannot run this type and both helpers are cut off, so the
    task must be recorded as lost - not left stuck in transit."""
    raw = deepcopy(BASE)
    raw["network"]["links"] = [
        {"from": "src", "to": "near", "profile": "none"},
        {"from": "src", "to": "far", "profile": "none"},
    ]
    result = Environment(parse_config(raw)).run()
    outcomes = result.outcomes
    assert outcomes, "expected the generator to produce tasks"
    assert all(o.task_lost for o in outcomes)
    assert all(o.selected_node is None for o in outcomes)
    assert all(o.deadline_met is False for o in outcomes)


def test_local_execution_never_needs_the_network():
    """A node can always run its own task, even with every link severed."""
    raw = deepcopy(BASE)
    raw["nodes"][0].pop("accepts_task_types")  # the source can run it now
    raw["network"]["links"] = [
        {"from": "src", "to": "near", "profile": "none"},
        {"from": "src", "to": "far", "profile": "none"},
    ]
    result = Environment(parse_config(raw)).run()
    assert not [o for o in result.outcomes if o.task_lost]
    assert {o.selected_node for o in result.outcomes} == {"src"}


def test_severing_is_directional_end_to_end():
    """Cutting far->src must not stop src->far being used for dispatch."""
    raw = deepcopy(BASE)
    raw["network"]["links"] = [{"from": "far", "to": "src", "profile": "none"}]
    result = Environment(parse_config(raw)).run()
    placements = {o.selected_node for o in result.outcomes if o.selected_node}
    assert "far" in placements


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_reachable_must_be_a_boolean():
    raw = deepcopy(BASE)
    raw["network"]["links"] = [{"from": "src", "to": "far", "reachable": "no"}]
    with pytest.raises(ConfigError, match="reachable must be true or false"):
        parse_config(raw)
