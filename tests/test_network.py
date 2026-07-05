"""Tests for network / transmission delay models."""

from __future__ import annotations

import pytest

from src.config.factory import network_models
from src.models import Task
from src.network import FluidLinkNetworkModel, InstantNetworkModel


def _task(data_size: float = 1_000_000.0) -> Task:
    return Task(
        task_id="t_1",
        arrival_time=0.0,
        task_type="compute",
        data_size=data_size,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="node_1",
    )


def test_instant_network_registered() -> None:
    assert "instant" in network_models


def test_fluid_link_registered() -> None:
    assert "fluid_link" in network_models


def test_instant_zero_delay() -> None:
    net = InstantNetworkModel()
    assert net.uplink_delay("a", "b", _task(), 0.0) == 0.0


def test_local_link_zero_delay() -> None:
    net = FluidLinkNetworkModel(default_profile="wifi")
    assert net.uplink_delay("n1", "n1", _task(), 0.0) == 0.0


def test_wifi_slower_than_lan_for_same_payload() -> None:
    lan = FluidLinkNetworkModel(default_profile="lan")
    wifi = FluidLinkNetworkModel(default_profile="wifi")
    task = _task(data_size=5_000_000.0)
    d_lan = lan.uplink_delay("node_1", "node_h", task, 0.0)
    d_wifi = wifi.uplink_delay("node_1", "node_h", task, 0.0)
    assert d_wifi > d_lan


def test_per_link_override() -> None:
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        links=[
            {"from": "node_1", "to": "node_h", "profile": "lan"},
        ],
    )
    task = _task(data_size=1_000_000.0)
    d = net.uplink_delay("node_1", "node_h", task, 0.0)
    d_default = FluidLinkNetworkModel(default_profile="wifi").uplink_delay(
        "node_1", "node_h", task, 0.0
    )
    assert d < d_default
