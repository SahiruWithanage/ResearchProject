"""Stage 7 trace-informed inputs: empirical/percentile distributions,
trace rate pattern, trace-driven link bandwidth, and the prepare tools."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from src.generation.distributions import build_distribution
from src.generation.rate_patterns import build_rate_pattern
from src.network import TraceFluidLinkNetworkModel
from src.models import Task

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _rng(seed: int = 1) -> np.random.Generator:
    return np.random.default_rng(seed)


def _task(data_size: float = 1_000_000.0) -> Task:
    return Task(
        task_id="t1",
        arrival_time=0.0,
        task_type="compute",
        data_size=data_size,
        cpu_demand=1.0,
        memory_demand=1.0,
        deadline=100.0,
        priority=1,
        source_node_id="n1",
    )


# ===========================================================================
# empirical distribution
# ===========================================================================

def test_empirical_samples_only_observed_values() -> None:
    d = build_distribution({"dist": "empirical", "values": [1.0, 2.0, 5.0]})
    rng = _rng()
    seen = {d.sample(rng) for _ in range(100)}
    assert seen <= {1.0, 2.0, 5.0}
    assert len(seen) > 1


def test_empirical_loads_csv_column(tmp_path: Path) -> None:
    f = tmp_path / "obs.csv"
    f.write_text("duration,other\n100,x\n200,y\n300,z\n", encoding="utf-8")
    d = build_distribution(
        {"dist": "empirical", "file": str(f), "column": "duration",
         "scale": 0.001}
    )
    assert {d.sample(_rng()) for _ in range(50)} <= {0.1, 0.2, 0.3}


def test_empirical_needs_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        build_distribution({"dist": "empirical"})


# ===========================================================================
# percentile distribution
# ===========================================================================

def test_percentile_stays_within_bounds_and_tracks_median() -> None:
    d = build_distribution(
        {
            "dist": "percentile",
            "points": [[0, 10], [25, 40], [50, 60], [75, 90], [100, 300]],
        }
    )
    rng = _rng()
    samples = [d.sample(rng) for _ in range(3000)]
    assert min(samples) >= 10 and max(samples) <= 300
    assert 40 <= float(np.median(samples)) <= 90  # near the p50 value


def test_percentile_scale_and_validation() -> None:
    d = build_distribution(
        {"dist": "percentile", "points": [[0, 100], [100, 200]], "scale": 0.01}
    )
    s = d.sample(_rng())
    assert 1.0 <= s <= 2.0
    with pytest.raises(ValueError, match="non-decreasing"):
        build_distribution(
            {"dist": "percentile", "points": [[0, 5], [50, 3], [100, 9]]}
        )


# ===========================================================================
# trace rate pattern
# ===========================================================================

def _rate_csv(tmp_path: Path) -> Path:
    f = tmp_path / "rate.csv"
    f.write_text("t,rate\n0,1.0\n60,3.0\n120,0.5\n", encoding="utf-8")
    return f


def test_trace_rate_steps_and_ends(tmp_path: Path) -> None:
    p = build_rate_pattern({"pattern": "trace", "file": str(_rate_csv(tmp_path))})
    assert p.rate(10.0) == 1.0
    assert p.rate(60.0) == 3.0
    assert p.rate(119.9) == 3.0
    assert p.rate(150.0) == 0.5
    assert p.rate(500.0) == 0.0  # past the end, no loop
    assert p.max_rate() == 3.0


def test_trace_rate_loop_and_scale(tmp_path: Path) -> None:
    p = build_rate_pattern(
        {"pattern": "trace", "file": str(_rate_csv(tmp_path)),
         "loop": True, "rate_scale": 2.0}
    )
    # duration = 120 + 60 = 180; t=190 wraps to t=10 -> rate 1.0 * 2.
    assert p.rate(190.0) == 2.0
    assert p.max_rate() == 6.0


# ===========================================================================
# trace-driven link bandwidth
# ===========================================================================

def _bw_csv(tmp_path: Path) -> Path:
    f = tmp_path / "bw.csv"
    # 8 Mbit/s for the first 10 s, then 0 (floored), then 80 Mbit/s.
    f.write_text(
        "t,bandwidth_bps\n0,8000000\n10,0\n20,80000000\n", encoding="utf-8"
    )
    return f


def _net(tmp_path: Path, **trace_kwargs) -> TraceFluidLinkNetworkModel:
    return TraceFluidLinkNetworkModel(
        traces=[
            {
                "from": "n1",
                "to": "n2",
                "file": str(_bw_csv(tmp_path)),
                **trace_kwargs,
            }
        ],
        default_profile="wifi",
        profiles={"wifi": {"jitter_s": 0.0, "base_latency_s": 0.0}},
    )


def test_trace_bandwidth_drives_transfer_time(tmp_path: Path) -> None:
    net = _net(tmp_path)
    task = _task(data_size=1_000_000.0)  # 8 Mbit
    assert net.expected_uplink_delay("n1", "n2", task, 5.0) == pytest.approx(1.0)
    assert net.expected_uplink_delay("n1", "n2", task, 25.0) == pytest.approx(0.1)


def test_trace_bandwidth_floor_prevents_infinite_transfer(tmp_path: Path) -> None:
    net = _net(tmp_path, min_bandwidth_bps=10_000.0)
    d = net.expected_uplink_delay("n1", "n2", _task(data_size=1000.0), 15.0)
    # 1000 B over the 10 kbit/s floor = 0.8 s - finite, not infinite.
    assert d == pytest.approx(0.8)


def test_untraced_links_fall_back_to_profile(tmp_path: Path) -> None:
    net = _net(tmp_path)
    # n2 -> n1 has no trace: plain wifi profile (50 Mbit/s, 0 latency here).
    d = net.expected_uplink_delay("n2", "n1", _task(data_size=1_000_000.0), 5.0)
    assert d == pytest.approx(8_000_000 / 50e6)


def test_trace_loop_wraps(tmp_path: Path) -> None:
    net = _net(tmp_path, loop=True)
    task = _task(data_size=1_000_000.0)
    # duration = 20 + 10 = 30; t=35 wraps to t=5 -> 8 Mbit/s -> 1.0 s.
    assert net.expected_uplink_delay("n1", "n2", task, 35.0) == pytest.approx(1.0)


# ===========================================================================
# prepare tools (run as scripts on fixture data)
# ===========================================================================

def test_prepare_ucc_5g_converts_and_filters(tmp_path: Path) -> None:
    src = tmp_path / "gnettrack.csv"
    src.write_text(
        "Timestamp,DL_bitrate,UL_bitrate,State\n"
        "a,1000,50,I\n"      # idle: dropped
        "b,20000,100,D\n"    # 20 Mbit/s
        "c,-,100,D\n"        # missing sample: dropped
        "d,40000,100,D\n",   # 40 Mbit/s
        encoding="utf-8",
    )
    out = tmp_path / "bw.csv"
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "prepare_ucc_5g.py"),
         str(src), str(out)],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stderr
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "t,bandwidth_bps"
    assert len(lines) == 3  # two D rows with values
    assert lines[1].split(",")[1] == repr(20000 * 1000.0)
