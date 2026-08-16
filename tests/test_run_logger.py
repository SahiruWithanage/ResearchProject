"""Tests for RunLogger: file shape, sorting, float precision, byte-identical reruns."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import parse_config
from src.logging_utils import RunLogger
from src.models import AllocationOutcome, NodeState
from src.simulation import Environment, EnvironmentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def raw_config() -> dict[str, Any]:
    return {
        "seed": 42,
        "sim_duration": 50.0,
        "dt": 1.0,
        "controllers": [
            {
                "id": "ctrl_main",
                "allocator": {"type": "local_first_helper_offload"},
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
                "source": {
                    "generator": {
                        "type": "poisson",
                        "rate": 0.6,
                        "cpu_demand": 2.0,
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
        "logging": {
            "output_dir": "logs/test",
            "log_state_every": 1.0,
        },
    }


def _run(raw: dict[str, Any]) -> tuple[EnvironmentResult, Any, dict[str, Any]]:
    raw_copy = copy.deepcopy(raw)
    cfg = parse_config(raw_copy)
    env = Environment(cfg)
    return env.run(), cfg, raw_copy


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _rows(path: Path) -> list[dict[str, str]]:
    """Data rows keyed by column name - survives columns being appended."""
    lines = _read_lines(path)
    header = lines[0].split(",")
    return [dict(zip(header, line.split(","))) for line in lines[1:]]


# ===========================================================================
# Directory and file presence
# ===========================================================================

def test_write_run_creates_output_dir(tmp_path: Path, raw_config) -> None:
    out = tmp_path / "fresh_dir"
    result, cfg, raw = _run(raw_config)
    RunLogger(out).write_run(result, cfg, raw)
    assert out.is_dir()


def test_write_run_creates_nested_output_dir(tmp_path: Path, raw_config) -> None:
    out = tmp_path / "level1" / "level2" / "run01"
    result, cfg, raw = _run(raw_config)
    RunLogger(out).write_run(result, cfg, raw)
    assert out.is_dir()


def test_manifest_records_code_environment_and_output_checksums(
    tmp_path: Path, raw_config
) -> None:
    """A result is untraceable without knowing what produced it."""
    import json

    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    m = json.loads(logger.manifest_path.read_text(encoding="utf-8"))

    assert m["run"]["seed"] == cfg.seed
    assert m["run"]["dt"] == cfg.dt
    assert m["run"]["allocators"] == ["local_first_helper_offload"]
    assert m["environment"]["python"]
    assert m["environment"]["packages"]["numpy"]
    assert "git_dirty" in m["code"]

    # checksums must match the files actually written
    import hashlib

    for name in ("allocation_log.csv", "state_log.csv", "config_used.yaml"):
        digest = hashlib.sha256(
            (logger.output_dir / name).read_bytes()
        ).hexdigest()
        assert m["outputs"][name] == digest


def test_manifest_checksums_prove_a_reproduction(tmp_path: Path, raw_config) -> None:
    """Same seed and config twice: identical output checksums."""
    import json

    digests = []
    for name in ("a", "b"):
        result, cfg, raw = _run(raw_config)
        logger = RunLogger(tmp_path / name)
        logger.write_run(result, cfg, raw)
        m = json.loads(logger.manifest_path.read_text(encoding="utf-8"))
        digests.append(m["outputs"])
    assert digests[0] == digests[1]


def test_write_run_produces_all_four_files(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    assert logger.allocation_log_path.is_file()
    assert logger.state_log_path.is_file()
    assert logger.config_path.is_file()
    assert logger.seed_path.is_file()


def test_write_run_overwrites_existing_files(tmp_path: Path, raw_config) -> None:
    out = tmp_path / "run"
    out.mkdir()
    (out / "allocation_log.csv").write_text("stale stale stale\n", encoding="utf-8")
    result, cfg, raw = _run(raw_config)
    RunLogger(out).write_run(result, cfg, raw)
    assert "stale" not in (out / "allocation_log.csv").read_text(encoding="utf-8")


# ===========================================================================
# allocation_log.csv shape and contents
# ===========================================================================

def test_allocation_log_has_explicit_header(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    header = _read_lines(logger.allocation_log_path)[0]
    expected = (
        "task_id,source_node_id,arrival_time,deadline,decision_time,"
        "allocator_type,selected_node,"
        "estimated_completion_time,transfer_start,transfer_end,compute_start,"
        "actual_completion_time,return_end,deadline_met,task_lost"
    )
    assert header == expected


def test_allocation_log_row_count_matches_outcomes(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    lines = _read_lines(logger.allocation_log_path)
    assert len(lines) == 1 + len(result.outcomes)  # +1 for header


def test_allocation_log_sorted_by_decision_time_then_task_id(
    tmp_path: Path, raw_config
) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    lines = _read_lines(logger.allocation_log_path)[1:]

    sort_keys = [
        (float(row["decision_time"]), row["task_id"])
        for row in _rows(logger.allocation_log_path)
    ]
    assert sort_keys == sorted(sort_keys)


def test_allocation_log_none_fields_become_empty_strings(tmp_path: Path) -> None:
    # A task that never completes: actual_completion_time and deadline_met are empty.
    outcome = AllocationOutcome(
        task_id="t_pending",
        decision_time=0.0,
        allocator_type="test",
        selected_node="node_1",
        estimated_completion_time=1.5,
        actual_completion_time=None,
        deadline_met=None,
    )
    result = EnvironmentResult(outcomes=[outcome], snapshots=[], final_time=10.0)

    cfg = parse_config(
        {
            "seed": 1,
            "sim_duration": 10.0,
            "dt": 1.0,
            "controllers": [
                {
                    "id": "c",
                    "allocator": {"type": "local_first_helper_offload"},
                    "manages": ["node_1", "node_h"],
                    "parent": None,
                }
            ],
            "nodes": [
                {
                    "id": "node_1",
                    "type": "source",
                    "cpu_capacity": 1.0,
                    "memory_capacity": 1.0,
                    "tier": "edge",
                    "source": {"generator": {"type": "poisson", "rate": 0.1}},
                },
                {
                    "id": "node_h",
                    "type": "helper",
                    "cpu_capacity": 1.0,
                    "memory_capacity": 1.0,
                    "tier": "edge",
                },
            ],
            "logging": {"output_dir": "logs/x", "log_state_every": 1.0},
        }
    )
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw_config={})
    row = _rows(logger.allocation_log_path)[0]
    # actual_completion_time, return_end, and deadline_met are empty;
    # task_lost is False.
    assert row["actual_completion_time"] == ""
    assert row["return_end"] == ""
    assert row["deadline_met"] == ""
    assert row["task_lost"] == "False"


def test_allocation_log_records_arrival_before_decision(
    tmp_path: Path, raw_config
) -> None:
    """Arrival is when the generator emitted the task; the controller decides
    at the next tick boundary, so arrival <= decision, within one dt."""
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    rows = _rows(logger.allocation_log_path)
    assert rows, "expected at least one task"
    for row in rows:
        arrival = float(row["arrival_time"])
        decision = float(row["decision_time"])
        assert arrival <= decision
        assert decision - arrival <= cfg.dt + 1e-9


def test_allocation_log_deadline_met_serialised_explicitly(tmp_path: Path) -> None:
    outcomes = [
        AllocationOutcome(
            task_id="t_met",
            decision_time=0.0,
            allocator_type="test",
            selected_node="n",
            estimated_completion_time=1.0,
            actual_completion_time=1.0,
            deadline_met=True,
        ),
        AllocationOutcome(
            task_id="t_missed",
            decision_time=0.0,
            allocator_type="test",
            selected_node="n",
            estimated_completion_time=1.0,
            actual_completion_time=99.0,
            deadline_met=False,
        ),
    ]
    result = EnvironmentResult(outcomes=outcomes, snapshots=[], final_time=10.0)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, _stub_config(), raw_config={})

    deadline_cols = [row["deadline_met"] for row in _rows(logger.allocation_log_path)]
    assert deadline_cols == ["True", "False"]


# ===========================================================================
# state_log.csv shape and contents
# ===========================================================================

def test_state_log_has_explicit_header(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    header = _read_lines(logger.state_log_path)[0]
    expected = (
        "time_step,node_id,queue_length,active_tasks,"
        "cpu_utilisation,memory_utilisation,reliability_score,failure_state,"
        "queued_work"
    )
    assert header == expected


def test_state_log_row_count_matches_snapshots(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    lines = _read_lines(logger.state_log_path)
    assert len(lines) == 1 + len(result.snapshots)


def test_state_log_sorted_by_time_then_node(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    lines = _read_lines(logger.state_log_path)[1:]
    sort_keys: list[tuple[float, str]] = []
    for line in lines:
        parts = line.split(",")
        sort_keys.append((float(parts[0]), parts[1]))
    assert sort_keys == sorted(sort_keys)


def test_state_log_float_round_trip_preserves_precision(tmp_path: Path) -> None:
    snapshots = [
        NodeState(
            time_step=1.0 / 3.0,
            node_id="n",
            queue_length=2,
            active_tasks=1,
            cpu_utilisation=0.123456789,
            memory_utilisation=0.987654321,
        )
    ]
    result = EnvironmentResult(outcomes=[], snapshots=snapshots, final_time=1.0)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, _stub_config(), raw_config={})

    row = _read_lines(logger.state_log_path)[1]
    parts = row.split(",")
    assert float(parts[0]) == 1.0 / 3.0
    assert float(parts[4]) == 0.123456789
    assert float(parts[5]) == 0.987654321


# ===========================================================================
# config_used.yaml round-trip
# ===========================================================================

def test_config_used_yaml_round_trips(tmp_path: Path, raw_config) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    loaded_back = yaml.safe_load(logger.config_path.read_text(encoding="utf-8"))
    assert loaded_back == raw


def test_config_used_yaml_re_parses_to_same_typed_config(
    tmp_path: Path, raw_config
) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    reloaded_raw = yaml.safe_load(logger.config_path.read_text(encoding="utf-8"))
    reloaded_cfg = parse_config(reloaded_raw)
    assert reloaded_cfg == cfg


# ===========================================================================
# seed.txt
# ===========================================================================

def test_seed_txt_contains_seed_and_trailing_newline(
    tmp_path: Path, raw_config
) -> None:
    result, cfg, raw = _run(raw_config)
    logger = RunLogger(tmp_path / "run")
    logger.write_run(result, cfg, raw)
    contents = logger.seed_path.read_text(encoding="utf-8")
    assert contents == "42\n"


# ===========================================================================
# Byte-identical outputs for same seed
# ===========================================================================

def test_two_runs_same_seed_produce_byte_identical_allocation_log(
    tmp_path: Path, raw_config
) -> None:
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    result_a, cfg_a, raw_a = _run(raw_config)
    result_b, cfg_b, raw_b = _run(raw_config)
    RunLogger(out_a).write_run(result_a, cfg_a, raw_a)
    RunLogger(out_b).write_run(result_b, cfg_b, raw_b)

    assert (out_a / "allocation_log.csv").read_bytes() == (
        out_b / "allocation_log.csv"
    ).read_bytes()


def test_two_runs_same_seed_produce_byte_identical_state_log(
    tmp_path: Path, raw_config
) -> None:
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    result_a, cfg_a, raw_a = _run(raw_config)
    result_b, cfg_b, raw_b = _run(raw_config)
    RunLogger(out_a).write_run(result_a, cfg_a, raw_a)
    RunLogger(out_b).write_run(result_b, cfg_b, raw_b)

    assert (out_a / "state_log.csv").read_bytes() == (
        out_b / "state_log.csv"
    ).read_bytes()


def test_two_runs_same_seed_produce_byte_identical_config_and_seed(
    tmp_path: Path, raw_config
) -> None:
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    result_a, cfg_a, raw_a = _run(raw_config)
    result_b, cfg_b, raw_b = _run(raw_config)
    RunLogger(out_a).write_run(result_a, cfg_a, raw_a)
    RunLogger(out_b).write_run(result_b, cfg_b, raw_b)

    assert (out_a / "config_used.yaml").read_bytes() == (
        out_b / "config_used.yaml"
    ).read_bytes()
    assert (out_a / "seed.txt").read_bytes() == (out_b / "seed.txt").read_bytes()


def test_different_seeds_produce_different_allocation_log(
    tmp_path: Path, raw_config
) -> None:
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    raw_a = copy.deepcopy(raw_config)
    raw_b = copy.deepcopy(raw_config)
    raw_b["seed"] = 43

    result_a, cfg_a, raw_a_loaded = _run(raw_a)
    result_b, cfg_b, raw_b_loaded = _run(raw_b)
    RunLogger(out_a).write_run(result_a, cfg_a, raw_a_loaded)
    RunLogger(out_b).write_run(result_b, cfg_b, raw_b_loaded)

    assert (out_a / "allocation_log.csv").read_bytes() != (
        out_b / "allocation_log.csv"
    ).read_bytes()


# ===========================================================================
# Helpers
# ===========================================================================

def _stub_config():
    return parse_config(
        {
            "seed": 7,
            "sim_duration": 10.0,
            "dt": 1.0,
            "controllers": [
                {
                    "id": "c",
                    "allocator": {"type": "local_first_helper_offload"},
                    "manages": ["n", "h"],
                    "parent": None,
                }
            ],
            "nodes": [
                {
                    "id": "n",
                    "type": "source",
                    "cpu_capacity": 1.0,
                    "memory_capacity": 1.0,
                    "tier": "edge",
                    "source": {"generator": {"type": "poisson", "rate": 0.1}},
                },
                {
                    "id": "h",
                    "type": "helper",
                    "cpu_capacity": 1.0,
                    "memory_capacity": 1.0,
                    "tier": "edge",
                },
            ],
            "logging": {"output_dir": "logs/x", "log_state_every": 1.0},
        }
    )
