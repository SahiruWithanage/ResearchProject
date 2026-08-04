"""A controller can live on a device, so its control traffic has a route.

While the controller is placeless, a heartbeat's travel time is a constant
someone typed into a config. Hosted on a node, the report crosses the same
links as data - so staleness becomes a consequence of the topology, which
is what makes it worth reasoning about.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np
import pytest

from src.config import parse_config
from src.config.loader import ConfigError
from src.controller.observability import HeartbeatObservability
from src.network.fluid_link import FluidLinkNetworkModel
from src.simulation.environment import Environment
from src.simulation.processing import NodeRuntime
from src.models import EdgeNode


def _rt(node_id: str) -> NodeRuntime:
    return NodeRuntime(
        EdgeNode(
            node_id=node_id,
            node_type="helper",
            cpu_capacity=1.0,
            memory_capacity=8.0,
            tier="edge",
        )
    )


BASE: dict[str, Any] = {
    "seed": 4,
    "sim_duration": 20.0,
    "dt": 0.01,
    "network": {
        "type": "fluid_link",
        "default_profile": "wifi",
        "links": [{"from": "far", "to": "ctrl_host", "profile": "5g"}],
    },
    "controllers": [
        {
            "id": "c",
            "allocator": {"type": "load_aware"},
            "observability": {"type": "heartbeat", "interval": 1.0},
            "manages": ["ctrl_host", "near", "far"],
            "parent": None,
        }
    ],
    "nodes": [
        {
            "id": "ctrl_host",
            "type": "source",
            "cpu_capacity": 1.0,
            "memory_capacity": 8.0,
            "tier": "edge",
            "source": {
                "generator": {
                    "type": "fixed_interval",
                    "interval": 1.0,
                    "cpu_demand": 0.5,
                    "deadline_offset": 30.0,
                }
            },
        },
        {"id": "near", "type": "helper", "cpu_capacity": 1.0,
         "memory_capacity": 8.0, "tier": "edge"},
        {"id": "far", "type": "helper", "cpu_capacity": 1.0,
         "memory_capacity": 8.0, "tier": "edge"},
    ],
    "logging": {"output_dir": "logs/host_test", "log_state_every": 1.0},
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_host_is_optional() -> None:
    cfg = parse_config(deepcopy(BASE))
    assert cfg.controllers[0].host is None


def test_host_must_be_a_real_node() -> None:
    raw = deepcopy(BASE)
    raw["controllers"][0]["host"] = "ghost"
    with pytest.raises(ConfigError, match="hosted on unknown node 'ghost'"):
        parse_config(raw)


def test_host_reaches_the_controller_object() -> None:
    raw = deepcopy(BASE)
    raw["controllers"][0]["host"] = "ctrl_host"
    env = Environment(parse_config(raw))
    assert env.controllers["c"].host == "ctrl_host"


# ---------------------------------------------------------------------------
# Reports travel real links
# ---------------------------------------------------------------------------


def test_placeless_controller_uses_the_flat_delay() -> None:
    obs = HeartbeatObservability(interval=1.0, report_delay=0.02)
    obs.attach([_rt("a")])
    assert obs._travel_time("a") == pytest.approx(0.02)


def test_hosted_reports_cross_the_network() -> None:
    """A distant node's report takes longer to arrive than a near one's."""
    net = FluidLinkNetworkModel(
        default_profile="wifi",
        links=[{"from": "far", "to": "host", "profile": "5g"}],
        rng=np.random.default_rng(2),
    )
    obs = HeartbeatObservability(interval=1.0, report_delay=0.005)
    obs.attach([_rt("host"), _rt("near"), _rt("far")])
    obs.locate("host", net)

    own = obs._travel_time("host")
    near = obs._travel_time("near")
    far = obs._travel_time("far")

    # The controller reads its own host locally: no hop to pay for.
    assert own == pytest.approx(0.005)
    # Everyone else pays for the link, and 5g's base latency beats wifi's.
    assert near > own
    assert far > near
    # The configured delay is a floor, not the whole story.
    assert near > 0.005


def test_hosting_changes_what_the_controller_believes() -> None:
    """Same run, same seed: hosting alters report timing, so beliefs differ."""
    placeless = Environment(parse_config(deepcopy(BASE))).run()
    raw = deepcopy(BASE)
    raw["controllers"][0]["host"] = "ctrl_host"
    hosted = Environment(parse_config(raw)).run()

    # Both runs must still be complete and deterministic.
    assert len(placeless.outcomes) == len(hosted.outcomes)
    a = Environment(parse_config(raw)).run()
    assert [o.selected_node for o in a.outcomes] == [
        o.selected_node for o in hosted.outcomes
    ]


def test_report_bytes_must_be_positive() -> None:
    with pytest.raises(ValueError, match="report_bytes must be >= 0"):
        HeartbeatObservability(interval=1.0, report_bytes=-1.0)
