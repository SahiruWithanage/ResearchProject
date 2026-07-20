"""Tests for network / transmission delay models."""

from __future__ import annotations

import numpy as np
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


def _rng() -> np.random.Generator:
    return np.random.default_rng(7)


def test_local_link_zero_delay() -> None:
    net = FluidLinkNetworkModel(default_profile="wifi", rng=_rng())
    assert net.uplink_delay("n1", "n1", _task(), 0.0) == 0.0


def test_wifi_slower_than_lan_for_same_payload() -> None:
    # 5 MB: transfer gap (~0.76 s) dwarfs the +-ms default jitter.
    lan = FluidLinkNetworkModel(default_profile="lan", rng=_rng())
    wifi = FluidLinkNetworkModel(default_profile="wifi", rng=_rng())
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
        rng=_rng(),
    )
    task = _task(data_size=1_000_000.0)
    d = net.uplink_delay("node_1", "node_h", task, 0.0)
    d_default = FluidLinkNetworkModel(
        default_profile="wifi", rng=_rng()
    ).uplink_delay("node_1", "node_h", task, 0.0)
    assert d < d_default


def test_jittery_links_without_rng_fail_fast() -> None:
    # Built-in wireless profiles default to jitter > 0: rng is mandatory.
    with pytest.raises(ValueError, match="no rng was provided"):
        FluidLinkNetworkModel(default_profile="wifi")


def test_zero_jitter_override_needs_no_rng() -> None:
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0}},
    )
    d = net.uplink_delay("node_1", "node_h", _task(data_size=1_000_000.0), 0.0)
    assert d == pytest.approx(0.010 + 1_000_000.0 * 8 / 50.0e6)


def test_transfer_time_is_bytes_times_eight_over_bits_per_second() -> None:
    # 1_000_000 bytes over 8 Mbit/s = exactly 1.0 s; zero base latency.
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        links=[
            {
                "from": "node_1",
                "to": "node_h",
                "bandwidth_bps": 8.0e6,
                "base_latency_s": 0.0,
            },
        ],
        rng=_rng(),
    )
    d = net.uplink_delay("node_1", "node_h", _task(data_size=1_000_000.0), 0.0)
    assert d == pytest.approx(1.0)


def test_expected_delay_is_deterministic_and_consumes_no_rng() -> None:
    rng = np.random.default_rng(7)
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.005}},
        rng=rng,
    )
    task = _task(data_size=1_000_000.0)

    state_before = repr(rng.bit_generator.state)
    estimates = [
        net.expected_uplink_delay("node_1", "node_h", task, 0.0) for _ in range(5)
    ]
    # Same value every time, and the rng state is untouched.
    assert len(set(estimates)) == 1
    assert repr(rng.bit_generator.state) == state_before

    # The realized delay does sample jitter (rng state advances).
    net.uplink_delay("node_1", "node_h", task, 0.0)
    assert repr(rng.bit_generator.state) != state_before


def test_realized_delay_centres_on_expected_delay() -> None:
    rng = np.random.default_rng(7)
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.005}},
        rng=rng,
    )
    task = _task(data_size=1_000_000.0)
    expected = net.expected_uplink_delay("node_1", "node_h", task, 0.0)
    realized = net.uplink_delay("node_1", "node_h", task, 0.0)
    assert abs(realized - expected) <= 0.005


# ===========================================================================
# Downlink (result return leg)
# ===========================================================================

def test_downlink_uses_result_size_not_data_size() -> None:
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0}},
    )
    task = _task(data_size=5_000_000.0)
    # data_size 5 MB but result_size 0: downlink = base latency only.
    d = net.downlink_delay("node_h", "node_1", task, 0.0)
    assert d == pytest.approx(0.010)

    task_with_result = Task(
        task_id="t_r",
        arrival_time=0.0,
        task_type="compute",
        data_size=5_000_000.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="node_1",
        result_size=1_000_000.0,
    )
    d_r = net.downlink_delay("node_h", "node_1", task_with_result, 0.0)
    assert d_r == pytest.approx(0.010 + 1_000_000.0 * 8 / 50.0e6)


def test_downlink_direction_resolves_independently() -> None:
    # Fast uplink to the helper, slow default coming back.
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0}, "lan": {"jitter_s": 0.0}},
        links=[{"from": "node_1", "to": "node_h", "profile": "lan"}],
    )
    task = Task(
        task_id="t_r",
        arrival_time=0.0,
        task_type="compute",
        data_size=1_000_000.0,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="node_1",
        result_size=1_000_000.0,
    )
    up = net.uplink_delay("node_1", "node_h", task, 0.0)
    down = net.downlink_delay("node_h", "node_1", task, 0.0)
    assert up < down  # lan up, wifi back


def test_instant_downlink_zero() -> None:
    from src.network import InstantNetworkModel

    assert InstantNetworkModel().downlink_delay("a", "b", _task(), 0.0) == 0.0


# ===========================================================================
# Time-varying fluid link
# ===========================================================================

def test_varying_registered() -> None:
    assert "varying_fluid_link" in network_models


def test_varying_quality_changes_between_windows() -> None:
    from src.network import VaryingFluidLinkNetworkModel

    net = VaryingFluidLinkNetworkModel(
        variation_period_s=10.0,
        bandwidth_variation=0.5,
        variation_entropy=123,
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0}},
    )
    task = _task(data_size=5_000_000.0)
    delays = {
        net.expected_uplink_delay("n1", "n2", task, t) for t in (0.0, 15.0, 25.0, 35.0)
    }
    assert len(delays) > 1  # different windows, different quality


def test_varying_is_deterministic_within_a_window_and_across_calls() -> None:
    from src.network import VaryingFluidLinkNetworkModel

    def build():
        return VaryingFluidLinkNetworkModel(
            variation_period_s=10.0,
            bandwidth_variation=0.5,
            variation_entropy=123,
            default_profile="wifi",
            profiles={"wifi": {"jitter_s": 0.0}},
        )

    task = _task(data_size=5_000_000.0)
    a, b = build(), build()
    for t in (0.0, 3.0, 9.99, 15.0, 100.0):
        assert a.expected_uplink_delay("n1", "n2", task, t) == pytest.approx(
            b.expected_uplink_delay("n1", "n2", task, t)
        )
    # Within one window the factor is constant.
    assert a.expected_uplink_delay("n1", "n2", task, 1.0) == pytest.approx(
        a.expected_uplink_delay("n1", "n2", task, 9.0)
    )


def test_varying_entropy_changes_the_weather() -> None:
    from src.network import VaryingFluidLinkNetworkModel

    def build(entropy: int):
        return VaryingFluidLinkNetworkModel(
            variation_period_s=10.0,
            bandwidth_variation=0.5,
            variation_entropy=entropy,
            default_profile="wifi",
            profiles={"wifi": {"jitter_s": 0.0}},
        )

    task = _task(data_size=5_000_000.0)
    a = [build(1).expected_uplink_delay("n1", "n2", task, t) for t in (0.0, 15.0, 25.0)]
    b = [build(2).expected_uplink_delay("n1", "n2", task, t) for t in (0.0, 15.0, 25.0)]
    assert a != b
