"""The random allocator: the floor of the comparison ladder.

Its job is to answer "what do you get without thinking?", so that a score
like 95% can be read as near-optimal or barely-better-than-guessing rather
than floating free.

The property that matters most here is that drawing randomness must not
disturb the world every other allocator faces.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

import numpy as np
import pytest
import yaml

from src.config import parse_config
from src.config.factory import allocators
from src.controller.allocators import RandomAllocator
from src.simulation.environment import Environment
from tests.alloc_helpers import decision_context
from src.models import EdgeNode, NodeState, Task


def _node(node_id: str) -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type="helper",
        cpu_capacity=2.0,
        memory_capacity=8.0,
        tier="edge",
    )


def _state(node_id: str) -> NodeState:
    return NodeState(
        time_step=0.0,
        node_id=node_id,
        queue_length=0,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
    )


def _task() -> Task:
    return Task(
        task_id="t1",
        source_node_id="src",
        arrival_time=0.0,
        deadline=100.0,
        data_size=1000,
        cpu_demand=1.0,
        memory_demand=1.0,
        task_type="compute",
        priority=1,
    )


def test_registered() -> None:
    assert "random" in allocators


def test_spreads_across_candidates() -> None:
    a = RandomAllocator()
    nodes = [_node("n1"), _node("n2"), _node("n3")]
    states = {n.node_id: _state(n.node_id) for n in nodes}
    rng = np.random.default_rng(724)
    picks = Counter(
        a.allocate(decision_context(_task(), nodes, states, rng=rng))
        for _ in range(600)
    )
    assert set(picks) == {"n1", "n2", "n3"}
    # roughly uniform: no node should take less than half a fair share
    assert min(picks.values()) > 600 / 3 * 0.5


def test_is_deterministic_for_a_seed() -> None:
    nodes = [_node("n1"), _node("n2"), _node("n3")]
    states = {n.node_id: _state(n.node_id) for n in nodes}

    def run(seed: int) -> list[str]:
        rng = np.random.default_rng(seed)
        a = RandomAllocator()
        return [
            a.allocate(decision_context(_task(), nodes, states, rng=rng))
            for _ in range(30)
        ]

    assert run(724) == run(724)
    assert run(724) != run(725)


def test_refuses_to_run_without_a_stream() -> None:
    """Silently falling back to 'first node' would be a disguised rule."""
    a = RandomAllocator()
    nodes = [_node("n1"), _node("n2")]
    states = {n.node_id: _state(n.node_id) for n in nodes}
    with pytest.raises(ValueError, match="needs a random stream"):
        a.allocate(decision_context(_task(), nodes, states))


def test_never_picks_an_ineligible_node() -> None:
    """Eligibility is the Controller's job and applies to every allocator;
    the floor is about decision quality, not about breaking constraints."""
    a = RandomAllocator()
    nodes = [_node("only")]
    states = {"only": _state("only")}
    rng = np.random.default_rng(1)
    for _ in range(20):
        assert a.allocate(decision_context(_task(), nodes, states, rng=rng)) == "only"


# ---------------------------------------------------------------------------
# The property that protects every other comparison
# ---------------------------------------------------------------------------


def _demo() -> dict[str, Any]:
    return yaml.safe_load(open("configs/demo.yaml", encoding="utf-8").read())


def test_allocator_randomness_does_not_disturb_the_world() -> None:
    """The random allocator draws once per task; the arrivals must not move.

    Task generators have their own streams, so a strategy that consumes
    randomness cannot shift when work appears. Without this the floor would
    be measured in a different world from everything it is compared against.
    """
    def arrivals(alloc: str) -> list[tuple[str, float]]:
        raw = deepcopy(_demo())
        raw["controllers"][0]["allocator"] = {"type": alloc}
        result = Environment(parse_config(raw)).run()
        return sorted(
            (o.task_id, round(o.arrival_time, 9)) for o in result.outcomes
        )

    assert arrivals("random") == arrivals("load_aware")


def test_random_run_is_reproducible() -> None:
    raw = deepcopy(_demo())
    raw["controllers"][0]["allocator"] = {"type": "random"}
    a = Environment(parse_config(raw)).run()
    b = Environment(parse_config(raw)).run()
    assert [o.selected_node for o in a.outcomes] == [
        o.selected_node for o in b.outcomes
    ]


def test_random_is_a_floor_not_a_contender() -> None:
    """It should place work in every node, and do worse than a strategy."""
    from ui.metrics import summarize

    def run(alloc: str):
        raw = deepcopy(_demo())
        raw["controllers"][0]["allocator"] = {"type": alloc}
        return summarize(Environment(parse_config(raw)).run())

    rnd = run("random")
    smart = run("weighted_score")
    assert rnd["success_rate"] < smart["success_rate"]
    # spreads work everywhere rather than concentrating it
    assert len(rnd["placement"]) >= 4
