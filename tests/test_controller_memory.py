"""The controller remembers what it dispatched.

A controller is stale about node *state*, but it is not amnesiac about its
own actions. Without that memory, tasks decided together all see the same
empty node and stampede onto it.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.controller.allocators import LoadAwareAllocator
from src.controller.controller import Controller
from src.controller.observability import HeartbeatObservability
from src.models import EdgeNode, Task
from src.network.fluid_link import FluidLinkNetworkModel
from src.simulation.estimates import CompletionEstimator
from src.simulation.processing import NodeRuntime


def _node(node_id: str, node_type: str = "helper") -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        node_type=node_type,
        cpu_capacity=2.0,
        memory_capacity=99.0,
        tier="edge",
    )


def _task(task_id: str, *, demand: float = 5.0, size: float = 2_000_000) -> Task:
    return Task(
        task_id=task_id,
        source_node_id="src",
        arrival_time=0.0,
        deadline=999.0,
        data_size=size,
        cpu_demand=demand,
        memory_demand=1.0,
        task_type="compute",
        priority=1,
    )


def _controller(runtimes, observability=None) -> Controller:
    return Controller(
        id="c",
        allocator=LoadAwareAllocator(),
        allocator_type="load_aware",
        managed_nodes=runtimes,
        observability=observability,
    )


def _estimator() -> CompletionEstimator:
    return CompletionEstimator(
        FluidLinkNetworkModel(default_profile="wifi", rng=np.random.default_rng(1))
    )


def test_simultaneous_decisions_do_not_stampede() -> None:
    """Six tasks decided at the same instant must spread, not pile up.

    None of them has reached a node yet, so without memory every one sees
    the same idle candidates and picks the same winner.
    """
    runtimes = [NodeRuntime(_node("h1")), NodeRuntime(_node("h2"))]
    ctrl = _controller(runtimes)
    est = _estimator()

    picks = [ctrl.submit(_task(f"t{i}"), 0.0, est).selected_node for i in range(6)]
    assert sorted(picks) == ["h1", "h1", "h1", "h2", "h2", "h2"]


def test_memory_is_bounded_by_reports() -> None:
    """A newer report supersedes the memory, so it cannot grow forever."""
    rt = NodeRuntime(_node("h1"))
    obs = HeartbeatObservability(interval=1.0)
    obs.attach([rt])
    obs.refresh(0.0)
    ctrl = _controller([rt], observability=obs)
    est = _estimator()

    for i in range(4):
        ctrl.submit(_task(f"t{i}"), 0.0, est)
    assert len(ctrl._in_flight) == 4

    # A report taken after those tasks should have landed makes them stale
    # knowledge: the node's own account now covers them.
    obs.refresh(50.0)
    ctrl.submit(_task("later"), 50.0, est)
    assert len(ctrl._in_flight) == 1  # only the one just dispatched


def test_pending_work_inflates_the_believed_backlog() -> None:
    """Dispatched work counts against a node until it reports back."""
    rt = NodeRuntime(_node("h1"))
    obs = HeartbeatObservability(interval=100.0)  # effectively never updates
    obs.attach([rt])
    obs.refresh(0.0)
    ctrl = _controller([rt], observability=obs)
    est = _estimator()

    believed_before = ctrl._adjusted_states(obs.observe(0.0))["h1"]
    assert believed_before.queued_work == pytest.approx(0.0)

    ctrl.submit(_task("t1", demand=7.0), 0.0, est)
    believed_after = ctrl._adjusted_states(obs.observe(0.0))["h1"]
    assert believed_after.queued_work == pytest.approx(7.0)
    assert believed_after.queue_length == 1


def test_local_placement_counts_immediately() -> None:
    """A task kept at home needs no transfer, so it is owed to that node now."""
    src = NodeRuntime(_node("src", node_type="source"))
    obs = HeartbeatObservability(interval=100.0)
    obs.attach([src])
    obs.refresh(0.0)
    ctrl = _controller([src], observability=obs)
    est = _estimator()

    out = ctrl.submit(_task("t1", demand=4.0), 0.0, est)
    assert out.selected_node == "src"
    entry = ctrl._in_flight[0]
    assert entry.node_id == "src"
    assert entry.expected_arrival == pytest.approx(0.0)  # no network hop


def test_in_transit_work_still_counts() -> None:
    """A task crossing the network is real work heading somewhere, and the
    destination has not seen it either - so it must stay counted."""
    runtimes = [NodeRuntime(_node("h1")), NodeRuntime(_node("h2"))]
    ctrl = _controller(runtimes)
    est = _estimator()

    ctrl.submit(_task("t1", demand=9.0), 0.0, est)
    entry = ctrl._in_flight[0]
    assert entry.expected_arrival > 0.0  # a wifi hop takes time
    believed = ctrl._adjusted_states(ctrl.observability.observe(0.0))
    assert believed[entry.node_id].queued_work == pytest.approx(9.0)
