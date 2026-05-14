"""Tests for the Poisson and FixedInterval task generators."""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.config.factory import generators
from src.generation import FixedIntervalGenerator, PoissonGenerator
from src.models import Task


# ===========================================================================
# Registration
# ===========================================================================

def test_poisson_is_registered() -> None:
    assert "poisson" in generators
    assert generators.get("poisson") is PoissonGenerator


def test_fixed_interval_is_registered() -> None:
    assert "fixed_interval" in generators
    assert generators.get("fixed_interval") is FixedIntervalGenerator


# ===========================================================================
# PoissonGenerator
# ===========================================================================

def test_poisson_requires_rng() -> None:
    with pytest.raises(ValueError, match="rng"):
        PoissonGenerator(rate=0.3, source_node_id="n_1")


def test_poisson_rate_must_be_non_negative() -> None:
    rng = np.random.default_rng(42)
    with pytest.raises(ValueError, match="rate"):
        PoissonGenerator(rate=-0.1, source_node_id="n_1", rng=rng)


def test_poisson_deadline_offset_must_be_positive() -> None:
    rng = np.random.default_rng(42)
    with pytest.raises(ValueError, match="deadline_offset"):
        PoissonGenerator(
            rate=0.3, source_node_id="n_1", rng=rng, deadline_offset=0
        )


def test_poisson_rate_zero_emits_nothing() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=0.0, source_node_id="n_1", rng=rng)
    assert gen.emit(0.0, 100.0) == []


def test_poisson_zero_width_window_emits_nothing() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=10.0, source_node_id="n_1", rng=rng)
    assert gen.emit(5.0, 5.0) == []


def test_poisson_emit_inverted_window_raises() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=1.0, source_node_id="n_1", rng=rng)
    with pytest.raises(ValueError, match="t_end must be >= t_start"):
        gen.emit(5.0, 3.0)


def test_poisson_all_arrivals_fall_inside_window() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=10.0, source_node_id="n_1", rng=rng)
    tasks = gen.emit(5.0, 10.0)
    assert len(tasks) > 0
    for t in tasks:
        assert 5.0 <= t.arrival_time < 10.0


def test_poisson_arrivals_are_sorted() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=10.0, source_node_id="n_1", rng=rng)
    tasks = gen.emit(0.0, 10.0)
    times = [t.arrival_time for t in tasks]
    assert times == sorted(times)


def test_poisson_mean_rate_is_correct() -> None:
    # rate=10 over 1000s -> mean 10000, sigma ~100. 5-sigma is safe for a single test.
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=10.0, source_node_id="n_1", rng=rng)
    tasks = gen.emit(0.0, 1000.0)
    assert abs(len(tasks) - 10_000) < 5 * 100


def test_poisson_stamps_source_node_id() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=5.0, source_node_id="node_xyz", rng=rng)
    tasks = gen.emit(0.0, 10.0)
    assert all(t.source_node_id == "node_xyz" for t in tasks)


def test_poisson_task_profile_defaults_match_methodology() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=5.0, source_node_id="n_1", rng=rng)
    tasks = gen.emit(0.0, 10.0)
    assert len(tasks) > 0
    sample = tasks[0]
    assert sample.task_type == "compute"
    assert sample.data_size == 1.0
    assert sample.cpu_demand == 1.0
    assert sample.memory_demand == 1.0
    assert sample.priority == 1


def test_poisson_task_profile_overrides_apply() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(
        rate=5.0,
        source_node_id="n_1",
        rng=rng,
        task_type="sensing",
        data_size=2.5,
        cpu_demand=3.0,
        memory_demand=4.0,
        deadline_offset=10.0,
        priority=7,
    )
    tasks = gen.emit(0.0, 5.0)
    assert len(tasks) > 0
    t = tasks[0]
    assert t.task_type == "sensing"
    assert t.data_size == 2.5
    assert t.cpu_demand == 3.0
    assert t.memory_demand == 4.0
    assert t.deadline == t.arrival_time + 10.0
    assert t.priority == 7


def test_poisson_same_seed_produces_identical_output() -> None:
    g1 = PoissonGenerator(
        rate=5.0, source_node_id="n_1", rng=np.random.default_rng(42)
    )
    g2 = PoissonGenerator(
        rate=5.0, source_node_id="n_1", rng=np.random.default_rng(42)
    )
    a = g1.emit(0.0, 20.0)
    b = g2.emit(0.0, 20.0)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert x.arrival_time == y.arrival_time
        assert x.task_id == y.task_id


def test_poisson_different_seeds_produce_different_output() -> None:
    g1 = PoissonGenerator(
        rate=5.0, source_node_id="n_1", rng=np.random.default_rng(42)
    )
    g2 = PoissonGenerator(
        rate=5.0, source_node_id="n_1", rng=np.random.default_rng(43)
    )
    a = [t.arrival_time for t in g1.emit(0.0, 20.0)]
    b = [t.arrival_time for t in g2.emit(0.0, 20.0)]
    assert a != b


def test_poisson_task_ids_are_unique_within_a_run() -> None:
    rng = np.random.default_rng(42)
    gen = PoissonGenerator(rate=10.0, source_node_id="n_1", rng=rng)
    tasks = gen.emit(0.0, 10.0) + gen.emit(10.0, 20.0)
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids))


# ===========================================================================
# FixedIntervalGenerator
# ===========================================================================

def test_fixed_interval_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="interval"):
        FixedIntervalGenerator(interval=0, source_node_id="n_1")


def test_fixed_interval_offset_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="offset"):
        FixedIntervalGenerator(interval=1.0, source_node_id="n_1", offset=-0.1)


def test_fixed_interval_emit_inverted_window_raises() -> None:
    gen = FixedIntervalGenerator(interval=1.0, source_node_id="n_1")
    with pytest.raises(ValueError, match="t_end must be >= t_start"):
        gen.emit(5.0, 3.0)


def test_fixed_interval_zero_width_window_emits_nothing() -> None:
    gen = FixedIntervalGenerator(interval=1.0, source_node_id="n_1")
    assert gen.emit(5.0, 5.0) == []


def test_fixed_interval_emits_at_regular_times_from_zero() -> None:
    gen = FixedIntervalGenerator(interval=2.0, source_node_id="n_1")
    tasks = gen.emit(0.0, 10.0)
    times = [t.arrival_time for t in tasks]
    assert times == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_fixed_interval_respects_offset() -> None:
    gen = FixedIntervalGenerator(interval=2.0, source_node_id="n_1", offset=1.0)
    tasks = gen.emit(0.0, 10.0)
    times = [t.arrival_time for t in tasks]
    assert times == [1.0, 3.0, 5.0, 7.0, 9.0]


def test_fixed_interval_window_skips_early_arrivals() -> None:
    gen = FixedIntervalGenerator(interval=2.0, source_node_id="n_1")
    tasks = gen.emit(5.0, 10.0)
    times = [t.arrival_time for t in tasks]
    assert times == [6.0, 8.0]


def test_fixed_interval_stamps_source_node_id() -> None:
    gen = FixedIntervalGenerator(interval=2.0, source_node_id="node_xyz")
    tasks = gen.emit(0.0, 10.0)
    assert all(t.source_node_id == "node_xyz" for t in tasks)


def test_fixed_interval_consecutive_windows_match_single_window() -> None:
    g_split = FixedIntervalGenerator(interval=2.0, source_node_id="n_1")
    g_whole = FixedIntervalGenerator(interval=2.0, source_node_id="n_1")

    split = g_split.emit(0.0, 5.0) + g_split.emit(5.0, 10.0)
    whole = g_whole.emit(0.0, 10.0)

    assert [t.arrival_time for t in split] == [t.arrival_time for t in whole]
    assert [t.task_id for t in split] == [t.task_id for t in whole]


def test_fixed_interval_task_ids_are_stable_across_chunkings() -> None:
    g_full = FixedIntervalGenerator(interval=1.0, source_node_id="n_1")
    full_ids = [t.task_id for t in g_full.emit(0.0, 5.0)]

    g_part = FixedIntervalGenerator(interval=1.0, source_node_id="n_1")
    part_ids = (
        [t.task_id for t in g_part.emit(0.0, 2.5)]
        + [t.task_id for t in g_part.emit(2.5, 5.0)]
    )

    assert full_ids == part_ids


def test_fixed_interval_ignores_rng_argument() -> None:
    rng = np.random.default_rng(42)
    g1 = FixedIntervalGenerator(interval=1.0, source_node_id="n_1", rng=rng)
    g2 = FixedIntervalGenerator(interval=1.0, source_node_id="n_1", rng=None)
    a = [t.arrival_time for t in g1.emit(0.0, 5.0)]
    b = [t.arrival_time for t in g2.emit(0.0, 5.0)]
    assert a == b


def test_fixed_interval_with_fractional_dt_compatible_offset() -> None:
    gen = FixedIntervalGenerator(interval=0.1, source_node_id="n_1")
    tasks = gen.emit(0.0, 1.0)
    times = [t.arrival_time for t in tasks]
    assert len(times) == 10
    for i, t in enumerate(times):
        assert math.isclose(t, i * 0.1, abs_tol=1e-9)
