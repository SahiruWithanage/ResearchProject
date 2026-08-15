"""Nodes can have a physical location, and the network uses it.

Two separate effects, both off unless configured:

- **propagation delay** - the signal takes time to cross the ground. Tiny at
  edge scale (about 5 microseconds per kilometre), decisive over hundreds of
  kilometres to a cloud tier.
- **path loss** - a wireless link weakens with distance, so the achievable
  rate drops. This is the effect the base paper (Zhai et al.) models, and
  the one large enough to change decisions.

Without `location` on the nodes, behaviour is exactly as before.
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


def _task(size: float = 500_000) -> Task:
    return Task(
        task_id="t1",
        source_node_id="a",
        arrival_time=0.0,
        deadline=100.0,
        data_size=size,
        cpu_demand=1.0,
        memory_demand=1.0,
        task_type="compute",
        priority=1,
    )


def _net(**kw: Any) -> FluidLinkNetworkModel:
    kw.setdefault("default_profile", "wifi")
    kw.setdefault("rng", np.random.default_rng(1))
    return FluidLinkNetworkModel(**kw)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


BASE: dict[str, Any] = {
    "seed": 3,
    "sim_duration": 10.0,
    "dt": 0.1,
    "network": {"type": "fluid_link", "default_profile": "wifi"},
    "controllers": [
        {
            "id": "c",
            "allocator": {"type": "load_aware"},
            "manages": ["src", "far"],
            "parent": None,
        }
    ],
    "nodes": [
        {
            "id": "src", "type": "source", "cpu_capacity": 1.0,
            "memory_capacity": 8.0, "tier": "edge", "location": [0.0, 0.0],
            "source": {"generator": {
                "type": "fixed_interval", "interval": 1.0,
                "cpu_demand": 0.5, "deadline_offset": 30.0}},
        },
        {
            "id": "far", "type": "helper", "cpu_capacity": 2.0,
            "memory_capacity": 8.0, "tier": "edge", "location": [3.0, 4.0],
        },
    ],
    "logging": {"output_dir": "logs/dist_test", "log_state_every": 1.0},
}


def test_location_is_optional() -> None:
    raw = deepcopy(BASE)
    for n in raw["nodes"]:
        n.pop("location")
    assert parse_config(raw).nodes[0].location is None


def test_location_is_parsed() -> None:
    cfg = parse_config(deepcopy(BASE))
    assert cfg.nodes[0].location == (0.0, 0.0)
    assert cfg.nodes[1].location == (3.0, 4.0)


def test_location_must_be_a_pair_of_numbers() -> None:
    raw = deepcopy(BASE)
    raw["nodes"][0]["location"] = [1.0, 2.0, 3.0]
    with pytest.raises(ConfigError, match=r"location must be \[x, y\]"):
        parse_config(raw)
    raw["nodes"][0]["location"] = "over there"
    with pytest.raises(ConfigError, match=r"location must be \[x, y\]"):
        parse_config(raw)


def test_positions_reach_the_network_model() -> None:
    env = Environment(parse_config(deepcopy(BASE)))
    # 3-4-5 triangle: the nodes are 5 km apart
    assert env._network._distance_km("src", "far") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Propagation delay
# ---------------------------------------------------------------------------


def test_no_geometry_means_no_distance_effect() -> None:
    """Nodes without a location behave exactly as before."""
    plain = _net()
    assert plain._distance_km("a", "b") == 0.0
    assert plain.expected_uplink_delay("a", "b", _task(), 0.0) == pytest.approx(
        0.010 + 500_000 * 8 / 50e6
    )


def test_propagation_adds_five_microseconds_per_km() -> None:
    net = _net(positions={"a": (0.0, 0.0), "b": (100.0, 0.0)})
    # 100 km at 200,000 km/s = 0.5 ms
    assert net._propagation_s(100.0) == pytest.approx(0.0005)
    near = _net(positions={"a": (0.0, 0.0), "b": (0.0, 0.0)})
    delta = net.expected_uplink_delay(
        "a", "b", _task(), 0.0
    ) - near.expected_uplink_delay("a", "b", _task(), 0.0)
    assert delta == pytest.approx(0.0005)


def test_propagation_is_negligible_at_edge_scale() -> None:
    """Honest about magnitude: 1 km costs 5 microseconds against a 90 ms
    transfer, so on its own distance will not move a result."""
    net = _net(positions={"a": (0.0, 0.0), "b": (1.0, 0.0)})
    total = net.expected_uplink_delay("a", "b", _task(), 0.0)
    assert net._propagation_s(1.0) / total < 0.001


# ---------------------------------------------------------------------------
# Path loss - the effect big enough to matter
# ---------------------------------------------------------------------------


def test_path_loss_is_off_by_default() -> None:
    net = _net(positions={"a": (0.0, 0.0), "b": (50.0, 0.0)})
    assert net.path_loss_exponent == 0.0
    spec = net._resolve_spec("a", "b")
    assert net._bandwidth_at(spec, 50.0) == spec.bandwidth_bps


def test_path_loss_reduces_bandwidth_with_distance() -> None:
    net = _net(
        positions={"a": (0.0, 0.0), "b": (1.0, 0.0)},
        path_loss_exponent=2.0,
        path_loss_reference_km=0.1,
    )
    spec = net._resolve_spec("a", "b")
    # 3x the reference distance, exponent 2 -> 1/9 of the rate (above the
    # 5% floor, so this exercises the scaling rather than the clamp)
    assert net._bandwidth_at(spec, 0.3) == pytest.approx(spec.bandwidth_bps / 9.0)
    # inside the reference distance, nothing changes
    assert net._bandwidth_at(spec, 0.05) == spec.bandwidth_bps
    # further away is always worse
    assert net._bandwidth_at(spec, 0.5) < net._bandwidth_at(spec, 0.3)


def test_path_loss_never_kills_a_link_silently() -> None:
    """Within range, distance degrades a link rather than ending it.

    Uses lan, whose range is unlimited, so this isolates the path-loss
    floor from the separate question of whether the medium reaches at all.
    """
    net = _net(
        default_profile="lan",
        positions={"a": (0.0, 0.0), "b": (10_000.0, 0.0)},
        path_loss_exponent=4.0,
        min_bandwidth_fraction=0.05,
    )
    spec = net._resolve_spec("a", "b")
    assert net._bandwidth_at(spec, 10_000.0) == pytest.approx(
        spec.bandwidth_bps * 0.05
    )
    assert net.can_reach("a", "b") is True
    assert net.expected_uplink_delay("a", "b", _task(), 0.0) < float("inf")


def test_distance_makes_a_far_node_genuinely_worse() -> None:
    net = _net(
        positions={"src": (0.0, 0.0), "near": (0.1, 0.0), "far": (2.0, 0.0)},
        path_loss_exponent=2.0,
    )
    near = net.expected_uplink_delay("src", "near", _task(), 0.0)
    far = net.expected_uplink_delay("src", "far", _task(), 0.0)
    assert far > near


# ---------------------------------------------------------------------------
# Range - wireless stops carrying at some distance
# ---------------------------------------------------------------------------


def test_wireless_has_a_range_and_wired_does_not() -> None:
    net = _net()
    assert net._profile_specs["wifi"].max_range_km == pytest.approx(0.15)
    assert net._profile_specs["5g"].max_range_km == pytest.approx(2.0)
    assert net._profile_specs["lan"].max_range_km == float("inf")


def test_a_node_beyond_wifi_range_is_unreachable() -> None:
    close = _net(positions={"a": (0.0, 0.0), "b": (0.10, 0.0)})
    far = _net(positions={"a": (0.0, 0.0), "b": (0.20, 0.0)})
    assert close.can_reach("a", "b") is True
    assert far.can_reach("a", "b") is False


def test_range_does_nothing_without_geometry() -> None:
    """Configs with no locations are untouched: everything is at distance 0."""
    assert _net().can_reach("a", "b") is True


def test_a_node_always_reaches_itself() -> None:
    net = _net(positions={"a": (0.0, 0.0)})
    assert net.can_reach("a", "a") is True


def test_range_can_be_overridden_per_profile() -> None:
    net = _net(
        profiles={"wifi": {"max_range_km": 1.0}},
        positions={"a": (0.0, 0.0), "b": (0.5, 0.0)},
    )
    assert net.can_reach("a", "b") is True


def test_out_of_range_task_is_lost_not_stranded() -> None:
    """The source cannot run this type and the only helper is too far, so
    the task must be recorded as lost rather than sitting in transit."""
    raw = deepcopy(BASE)
    raw["nodes"][0]["accepts_task_types"] = ["other"]  # cannot run its own work
    raw["nodes"][0]["source"]["generator"]["task_type"] = "compute"
    raw["nodes"][1]["location"] = [5.0, 0.0]  # far beyond wifi's 150 m
    result = Environment(parse_config(raw)).run()
    assert result.outcomes
    assert all(o.task_lost for o in result.outcomes)
    assert all(o.selected_node is None for o in result.outcomes)


def test_moving_a_node_into_range_restores_it() -> None:
    raw = deepcopy(BASE)
    raw["nodes"][0]["accepts_task_types"] = ["other"]
    raw["nodes"][0]["source"]["generator"]["task_type"] = "compute"
    raw["nodes"][1]["location"] = [0.05, 0.0]  # within 150 m
    result = Environment(parse_config(raw)).run()
    assert not [o for o in result.outcomes if o.task_lost]
    assert {o.selected_node for o in result.outcomes} == {"far"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_parameter_validation() -> None:
    with pytest.raises(ValueError, match="propagation_speed_kms must be > 0"):
        _net(propagation_speed_kms=0)
    with pytest.raises(ValueError, match="path_loss_exponent must be >= 0"):
        _net(path_loss_exponent=-1)
    with pytest.raises(ValueError, match="path_loss_reference_km must be > 0"):
        _net(path_loss_reference_km=0)
    with pytest.raises(ValueError, match="min_bandwidth_fraction must be in"):
        _net(min_bandwidth_fraction=0.0)


def test_existing_configs_are_unaffected() -> None:
    """No location anywhere means byte-identical behaviour to before."""
    import yaml

    raw = yaml.safe_load(
        open("configs/demo.yaml", encoding="utf-8").read()
    )
    assert not any("location" in n for n in raw["nodes"])
    env = Environment(parse_config(raw))
    assert env._network._positions == {}
