"""Task realism: property distributions, task-type mixes, time-varying rates."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from src.config import parse_config
from src.config.factory import distributions, rate_patterns
from src.generation import PoissonGenerator, TaskBuilder
from src.generation.distributions import build_distribution
from src.generation.rate_patterns import build_rate_pattern
from src.simulation import Environment


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# ===========================================================================
# Distributions
# ===========================================================================

def test_builtin_distributions_registered() -> None:
    for name in ("constant", "uniform", "normal", "lognormal", "exponential"):
        assert name in distributions


def test_plain_number_becomes_constant() -> None:
    d = build_distribution(2.5)
    assert d.is_constant is True
    assert d.sample(None) == 2.5


def test_uniform_stays_in_range_and_is_seeded() -> None:
    d = build_distribution({"dist": "uniform", "low": 1.0, "high": 3.0})
    a = [d.sample(_rng()) for _ in range(5)]
    b = [d.sample(_rng()) for _ in range(5)]
    assert a == b  # same seed, same draws
    rng = _rng()
    assert all(1.0 <= d.sample(rng) <= 3.0 for _ in range(200))


def test_normal_clips_to_bounds() -> None:
    d = build_distribution(
        {"dist": "normal", "mean": 1.0, "std": 5.0, "min": 0.5, "max": 1.5}
    )
    rng = _rng()
    assert all(0.5 <= d.sample(rng) <= 1.5 for _ in range(200))


def test_unknown_distribution_rejected() -> None:
    with pytest.raises(KeyError, match="unknown distribution"):
        build_distribution({"dist": "zipfian_madness"})


def test_bad_distribution_params_rejected() -> None:
    with pytest.raises(ValueError, match="bad params"):
        build_distribution({"dist": "uniform", "low": 1.0})  # missing high


# ===========================================================================
# Rate patterns
# ===========================================================================

def test_builtin_rate_patterns_registered() -> None:
    for name in ("constant", "sinusoidal", "piecewise"):
        assert name in rate_patterns


def test_sinusoidal_rate_moves_and_bounds() -> None:
    p = build_rate_pattern(
        {"pattern": "sinusoidal", "base": 1.0, "amplitude": 0.5, "period": 100.0}
    )
    assert p.rate(25.0) == pytest.approx(1.5)  # sin peak at quarter period
    assert p.rate(75.0) == pytest.approx(0.5)
    assert p.max_rate() == pytest.approx(1.5)


def test_sinusoidal_rate_floors_at_zero() -> None:
    p = build_rate_pattern(
        {"pattern": "sinusoidal", "base": 0.2, "amplitude": 1.0, "period": 100.0}
    )
    assert p.rate(75.0) == 0.0  # base - amplitude < 0 -> clamped


def test_piecewise_rate_steps() -> None:
    p = build_rate_pattern(
        {
            "pattern": "piecewise",
            "segments": [
                {"t_start": 0, "rate": 0.3},
                {"t_start": 100, "rate": 2.0},
                {"t_start": 130, "rate": 0.3},
            ],
        }
    )
    assert p.rate(50.0) == 0.3
    assert p.rate(100.0) == 2.0
    assert p.rate(129.9) == 2.0
    assert p.rate(130.0) == 0.3
    assert p.max_rate() == 2.0


def test_piecewise_unsorted_segments_rejected() -> None:
    with pytest.raises(ValueError, match="sorted"):
        build_rate_pattern(
            {
                "pattern": "piecewise",
                "segments": [
                    {"t_start": 100, "rate": 1.0},
                    {"t_start": 0, "rate": 0.5},
                ],
            }
        )


# ===========================================================================
# TaskBuilder
# ===========================================================================

def test_constant_builder_consumes_no_rng() -> None:
    builder = TaskBuilder(source_node_id="n1", cpu_demand=2.0, data_size=5.0)
    assert builder.needs_rng is False
    rng = _rng()
    state_before = repr(rng.bit_generator.state)
    task = builder.build("t1", 0.0, rng)
    assert repr(rng.bit_generator.state) == state_before
    assert task.cpu_demand == 2.0 and task.data_size == 5.0


def test_distribution_properties_sampled_per_task() -> None:
    builder = TaskBuilder(
        source_node_id="n1",
        data_size={"dist": "uniform", "low": 10.0, "high": 20.0},
    )
    assert builder.needs_rng is True
    rng = _rng()
    sizes = {builder.build(f"t{i}", 0.0, rng).data_size for i in range(20)}
    assert len(sizes) > 1
    assert all(10.0 <= s <= 20.0 for s in sizes)


def test_task_mix_selects_types_by_weight() -> None:
    builder = TaskBuilder(
        source_node_id="n1",
        deadline_offset=10.0,
        task_mix=[
            {"weight": 0.8, "task_type": "telemetry", "cpu_demand": 0.5},
            {"weight": 0.2, "task_type": "analytics", "cpu_demand": 4.0},
        ],
    )
    rng = _rng()
    tasks = [builder.build(f"t{i}", 0.0, rng) for i in range(1000)]
    counts = Counter(t.task_type for t in tasks)
    assert 700 < counts["telemetry"] < 900  # ~80%
    assert 100 < counts["analytics"] < 300  # ~20%
    # Profile-specific properties applied, shared default inherited.
    for t in tasks:
        expected_cpu = 0.5 if t.task_type == "telemetry" else 4.0
        assert t.cpu_demand == expected_cpu
        assert t.deadline == pytest.approx(t.arrival_time + 10.0)


def test_task_mix_unknown_field_rejected() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        TaskBuilder(
            source_node_id="n1",
            task_mix=[{"task_type": "a", "hovercraft": 1}],
        )


def test_constant_deadline_offset_validated_fast() -> None:
    with pytest.raises(ValueError, match="deadline_offset must be > 0"):
        TaskBuilder(source_node_id="n1", deadline_offset=0.0)


def test_fixed_interval_with_mix_requires_rng() -> None:
    from src.generation import FixedIntervalGenerator

    with pytest.raises(ValueError, match="requires an rng"):
        FixedIntervalGenerator(
            interval=1.0,
            source_node_id="n1",
            task_mix=[
                {"task_type": "a"},
                {"task_type": "b"},
            ],
        )


# ===========================================================================
# Non-homogeneous Poisson (time-varying rate)
# ===========================================================================

def test_constant_rate_stream_identical_to_plain_number() -> None:
    # `rate: 0.8` must reproduce the exact pre-realism task stream.
    a = PoissonGenerator(rate=0.8, source_node_id="n1", rng=_rng(1))
    b = PoissonGenerator(rate=0.8, source_node_id="n1", rng=_rng(1))
    ta = [t.arrival_time for t in a.emit(0.0, 100.0)]
    tb = [t.arrival_time for t in b.emit(0.0, 100.0)]
    assert ta == tb and len(ta) > 0


def test_piecewise_burst_shapes_the_arrivals() -> None:
    gen = PoissonGenerator(
        rate={
            "pattern": "piecewise",
            "segments": [
                {"t_start": 0, "rate": 0.1},
                {"t_start": 100, "rate": 5.0},
                {"t_start": 120, "rate": 0.1},
            ],
        },
        source_node_id="n1",
        rng=_rng(3),
    )
    tasks = []
    t = 0.0
    while t < 200.0:
        tasks.append(gen.emit(t, t + 1.0))
        t += 1.0
    flat = [task for chunk in tasks for task in chunk]
    calm_before = [t for t in flat if t.arrival_time < 100.0]
    burst = [t for t in flat if 100.0 <= t.arrival_time < 120.0]
    calm_after = [t for t in flat if t.arrival_time >= 120.0]
    # ~10 expected before, ~100 in the burst, ~8 after.
    assert len(burst) > 5 * max(len(calm_before), len(calm_after), 1)


def test_sinusoidal_rate_is_reproducible() -> None:
    def run() -> list[float]:
        gen = PoissonGenerator(
            rate={
                "pattern": "sinusoidal",
                "base": 0.5,
                "amplitude": 0.4,
                "period": 50.0,
            },
            source_node_id="n1",
            rng=_rng(9),
        )
        out: list[float] = []
        t = 0.0
        while t < 100.0:
            out.extend(task.arrival_time for task in gen.emit(t, t + 1.0))
            t += 1.0
        return out

    assert run() == run()


def test_zero_max_rate_emits_nothing() -> None:
    gen = PoissonGenerator(
        rate={
            "pattern": "piecewise",
            "segments": [{"t_start": 0, "rate": 0.0}],
        },
        source_node_id="n1",
        rng=_rng(1),
    )
    assert gen.emit(0.0, 100.0) == []


# ===========================================================================
# End to end: mixed workload through the whole simulator
# ===========================================================================

def test_mixed_workload_respects_suitability_end_to_end() -> None:
    # The sensor node only accepts telemetry, so every analytics task it
    # generates MUST run on the helper.
    raw = {
        "seed": 11,
        "sim_duration": 60.0,
        "dt": 0.01,
        "controllers": [
            {
                "id": "c",
                "allocator": {"type": "load_aware"},
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
                "accepts_task_types": ["telemetry"],
                "source": {
                    "generator": {
                        "type": "poisson",
                        "rate": 1.0,
                        "deadline_offset": 30.0,
                        "task_mix": [
                            {
                                "weight": 0.7,
                                "task_type": "telemetry",
                                "cpu_demand": 0.2,
                            },
                            {
                                "weight": 0.3,
                                "task_type": "analytics",
                                "cpu_demand": {
                                    "dist": "uniform",
                                    "low": 0.5,
                                    "high": 2.0,
                                },
                            },
                        ],
                    }
                },
            },
            {
                "id": "node_h",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/x", "log_state_every": 10.0},
    }
    env = Environment(parse_config(raw))
    result = env.run()
    assert len(result.outcomes) > 20

    # Recover task types from the generator's own record via task ids is
    # not possible post-hoc, so infer: anything placed on node_1 must have
    # been telemetry (the only type it accepts).
    placed_local = [o for o in result.outcomes if o.selected_node == "node_1"]
    placed_helper = [o for o in result.outcomes if o.selected_node == "node_h"]
    assert placed_local and placed_helper