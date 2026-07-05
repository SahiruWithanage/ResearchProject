"""Tests for the Phase 1 scaffold allocator (local-first / helper-offload)."""

from __future__ import annotations

import pytest

from src.config.factory import allocators
from src.controller.allocators import LocalFirstHelperOffloadAllocator
from src.models import EdgeNode, NodeState, Task
from tests.alloc_helpers import decision_context


def _alloc(a, task, candidates, states, t=0.0):
    return a.allocate(decision_context(task, list(candidates), states, t))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(source_node_id: str | None = "node_1") -> Task:
    return Task(
        task_id="t_001",
        arrival_time=0.0,
        task_type="compute",
        data_size=1.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=5.0,
        priority=1,
        source_node_id=source_node_id,
    )


def _node(node_id: str, node_type: str = "source") -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type=node_type,
        cpu_capacity=4.0,
        memory_capacity=8.0,
        tier="edge",
    )


def _state(node_id: str, queue_length: int = 0) -> NodeState:
    return NodeState(
        time_step=0.0,
        node_id=node_id,
        queue_length=queue_length,
        active_tasks=0,
        cpu_utilisation=0.0,
        memory_utilisation=0.0,
    )


# ===========================================================================
# Registration & construction
# ===========================================================================

def test_allocator_is_registered() -> None:
    assert "local_first_helper_offload" in allocators
    assert allocators.get("local_first_helper_offload") is \
        LocalFirstHelperOffloadAllocator


def test_default_max_local_queue_is_three() -> None:
    a = LocalFirstHelperOffloadAllocator()
    assert a.max_local_queue == 3


def test_max_local_queue_can_be_configured() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=10)
    assert a.max_local_queue == 10


def test_negative_max_local_queue_raises() -> None:
    with pytest.raises(ValueError, match="max_local_queue"):
        LocalFirstHelperOffloadAllocator(max_local_queue=-1)


def test_empty_candidates_raises() -> None:
    a = LocalFirstHelperOffloadAllocator()
    with pytest.raises(ValueError, match="at least one candidate"):
        a.allocate(decision_context(_task(), [], {}))


# ===========================================================================
# Branch (a): source has capacity -> keep local
# ===========================================================================

def test_keeps_task_local_when_source_has_capacity() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [_node("node_1"), _node("node_h", "helper")]
    states = {"node_1": _state("node_1", queue_length=0),
              "node_h": _state("node_h", queue_length=0)}

    chosen = _alloc(a, _task(source_node_id="node_1"), candidates, states)
    assert chosen == "node_1"


def test_keeps_task_local_just_below_threshold() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [_node("node_1"), _node("node_h", "helper")]
    states = {"node_1": _state("node_1", queue_length=2),
              "node_h": _state("node_h", queue_length=0)}

    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_1"


# ===========================================================================
# Branch (b): source is over threshold -> offload to helper
# ===========================================================================

def test_offloads_to_helper_when_source_is_at_threshold() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [_node("node_1"), _node("node_h", "helper")]
    states = {"node_1": _state("node_1", queue_length=3),
              "node_h": _state("node_h", queue_length=0)}

    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_h"


def test_offloads_to_helper_when_source_is_over_threshold() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [_node("node_1"), _node("node_h", "helper")]
    states = {"node_1": _state("node_1", queue_length=10),
              "node_h": _state("node_h", queue_length=2)}

    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_h"


def test_prefers_helper_over_other_source_when_offloading() -> None:
    # When the source is over threshold, another source must not catch the task if a helper exists.
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [
        _node("node_1"),
        _node("node_2"),
        _node("node_h", "helper"),
    ]
    states = {
        "node_1": _state("node_1", queue_length=5),
        "node_2": _state("node_2", queue_length=0),
        "node_h": _state("node_h", queue_length=1),
    }
    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_h"


def test_picks_shortest_queue_among_multiple_helpers() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [
        _node("node_1"),
        _node("node_h1", "helper"),
        _node("node_h2", "helper"),
    ]
    states = {
        "node_1": _state("node_1", queue_length=10),
        "node_h1": _state("node_h1", queue_length=5),
        "node_h2": _state("node_h2", queue_length=2),
    }
    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_h2"


def test_helper_tie_broken_deterministically_by_id() -> None:
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [
        _node("node_1"),
        _node("node_hb", "helper"),
        _node("node_ha", "helper"),
    ]
    states = {
        "node_1": _state("node_1", queue_length=10),
        "node_hb": _state("node_hb", queue_length=0),
        "node_ha": _state("node_ha", queue_length=0),
    }
    chosen = _alloc(a, _task(source_node_id="node_1"), candidates, states)
    # Lexicographically smaller id wins the tie.
    assert chosen == "node_ha"


# ===========================================================================
# Branch (c): no helper available -> deterministic fallback
# ===========================================================================

def test_falls_back_to_shortest_queue_when_no_helper() -> None:
    # No helper available: pick the shortest queue across remaining candidates.
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [_node("node_1"), _node("node_2"), _node("node_3")]
    states = {
        "node_1": _state("node_1", queue_length=10),
        "node_2": _state("node_2", queue_length=4),
        "node_3": _state("node_3", queue_length=1),
    }
    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_3"


def test_falls_back_to_source_if_only_candidate_when_overloaded() -> None:
    # Only candidate is the overloaded source: still return a deterministic answer.
    a = LocalFirstHelperOffloadAllocator(max_local_queue=3)
    candidates = [_node("node_1")]
    states = {"node_1": _state("node_1", queue_length=99)}
    assert _alloc(a, _task(source_node_id="node_1"), candidates, states) == "node_1"


# ===========================================================================
# Task with no source_node_id (e.g. trace replay without origin info)
# ===========================================================================

def test_task_without_source_node_id_skips_local_branch() -> None:
    a = LocalFirstHelperOffloadAllocator()
    candidates = [_node("node_1"), _node("node_h", "helper")]
    states = {
        "node_1": _state("node_1", queue_length=0),
        "node_h": _state("node_h", queue_length=5),
    }
    chosen = _alloc(a, _task(source_node_id=None), candidates, states)
    # Even though node_1 has the shorter queue, the helper is still preferred
    # since the rule's offload branch is taken (no source to keep local on).
    assert chosen == "node_h"


def test_task_whose_source_is_not_in_candidates_offloads() -> None:
    a = LocalFirstHelperOffloadAllocator()
    candidates = [_node("node_2"), _node("node_h", "helper")]
    states = {
        "node_2": _state("node_2", queue_length=0),
        "node_h": _state("node_h", queue_length=2),
    }
    chosen = _alloc(a, _task(source_node_id="node_1"), candidates, states)
    assert chosen == "node_h"
