"""Tests for the Phase 1 CLI entry point (experiments/run_phase1.py)."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from experiments.run_phase1 import main
from src.config import parse_config
from src.logging_utils import RunLogger
from src.simulation import Environment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def raw_config() -> dict[str, Any]:
    return {
        "seed": 42,
        "sim_duration": 30.0,
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
            "output_dir": "logs/test_cli",
            "log_state_every": 1.0,
        },
    }


def _write_yaml(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


# ===========================================================================
# End-to-end happy path
# ===========================================================================

def test_main_returns_zero_on_successful_run(
    tmp_path: Path, raw_config, capsys
) -> None:
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "run"
    rc = main([str(cfg_path), "--output-dir", str(out_dir)])
    assert rc == 0


def test_main_writes_all_four_output_files(
    tmp_path: Path, raw_config, capsys
) -> None:
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "run"
    main([str(cfg_path), "--output-dir", str(out_dir)])
    assert (out_dir / "allocation_log.csv").is_file()
    assert (out_dir / "state_log.csv").is_file()
    assert (out_dir / "config_used.yaml").is_file()
    assert (out_dir / "seed.txt").is_file()


def test_main_outputs_match_direct_run_byte_for_byte(
    tmp_path: Path, raw_config, capsys
) -> None:
    # The CLI must produce the same bytes as a direct programmatic run with the same seed.
    cfg_path = _write_yaml(tmp_path, raw_config)
    cli_out = tmp_path / "cli"
    main([str(cfg_path), "--output-dir", str(cli_out), "--quiet"])

    direct_out = tmp_path / "direct"
    raw = copy.deepcopy(raw_config)
    raw["logging"]["output_dir"] = str(direct_out)
    cfg = parse_config(raw)
    result = Environment(cfg).run()
    RunLogger(direct_out).write_run(result, cfg, raw)

    assert (cli_out / "allocation_log.csv").read_bytes() == (
        direct_out / "allocation_log.csv"
    ).read_bytes()
    assert (cli_out / "state_log.csv").read_bytes() == (
        direct_out / "state_log.csv"
    ).read_bytes()


# ===========================================================================
# Stdout summary
# ===========================================================================

def test_main_prints_summary_in_normal_mode(
    tmp_path: Path, raw_config, capsys
) -> None:
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "run"
    rc = main([str(cfg_path), "--output-dir", str(out_dir)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Loading config" in captured.out
    assert "Run complete" in captured.out
    assert "Tasks generated" in captured.out
    assert "Allocations per node" in captured.out
    assert "Output written to" in captured.out


def test_main_quiet_suppresses_stdout(
    tmp_path: Path, raw_config, capsys
) -> None:
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "run"
    rc = main([str(cfg_path), "--output-dir", str(out_dir), "--quiet"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""


# ===========================================================================
# CLI overrides
# ===========================================================================

def test_main_seed_override_changes_outputs(
    tmp_path: Path, raw_config, capsys
) -> None:
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_a = tmp_path / "seed_42"
    out_b = tmp_path / "seed_7"
    main([str(cfg_path), "--output-dir", str(out_a), "--quiet"])
    main([str(cfg_path), "--output-dir", str(out_b), "--seed", "7", "--quiet"])

    assert (out_a / "seed.txt").read_text(encoding="utf-8") == "42\n"
    assert (out_b / "seed.txt").read_text(encoding="utf-8") == "7\n"
    assert (out_a / "allocation_log.csv").read_bytes() != (
        out_b / "allocation_log.csv"
    ).read_bytes()


def test_main_output_dir_override_used_for_files(
    tmp_path: Path, raw_config, capsys
) -> None:
    raw_config["logging"]["output_dir"] = "logs/will_be_ignored"
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "elsewhere"
    main([str(cfg_path), "--output-dir", str(out_dir), "--quiet"])
    assert (out_dir / "allocation_log.csv").is_file()
    assert not (tmp_path / "logs" / "will_be_ignored").exists()


def test_main_output_dir_override_recorded_in_config_used(
    tmp_path: Path, raw_config, capsys
) -> None:
    # The override is captured in config_used.yaml so the file round-trips to a runnable config.
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "elsewhere"
    main([str(cfg_path), "--output-dir", str(out_dir), "--quiet"])
    written = yaml.safe_load((out_dir / "config_used.yaml").read_text(encoding="utf-8"))
    assert written["logging"]["output_dir"] == str(out_dir)


# ===========================================================================
# Error paths
# ===========================================================================

def test_main_missing_config_file_returns_2(tmp_path: Path, capsys) -> None:
    rc = main([str(tmp_path / "does_not_exist.yaml")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "config file not found" in captured.err


def test_main_malformed_yaml_returns_2(tmp_path: Path, capsys) -> None:
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("seed: 42\n  bad:\n - indent\n", encoding="utf-8")
    rc = main([str(cfg_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "failed to parse YAML" in captured.err


def test_main_top_level_list_returns_2(tmp_path: Path, capsys) -> None:
    cfg_path = tmp_path / "list.yaml"
    cfg_path.write_text("- a\n- b\n", encoding="utf-8")
    rc = main([str(cfg_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "must be a mapping" in captured.err


def test_main_invalid_schema_returns_2(
    tmp_path: Path, raw_config, capsys
) -> None:
    del raw_config["seed"]
    cfg_path = _write_yaml(tmp_path, raw_config)
    rc = main([str(cfg_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "invalid config" in captured.err


# ===========================================================================
# Subprocess smoke test
# ===========================================================================

def test_cli_subprocess_smoke(tmp_path: Path, raw_config) -> None:
    # Actually invoke the CLI in a subprocess to verify sys.path bootstrap + argparse + exit code.
    cfg_path = _write_yaml(tmp_path, raw_config)
    out_dir = tmp_path / "run"
    project_root = Path(__file__).resolve().parent.parent

    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "experiments" / "run_phase1.py"),
            str(cfg_path),
            "--output-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"CLI failed: stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    assert (out_dir / "allocation_log.csv").is_file()
    assert (out_dir / "state_log.csv").is_file()
    assert (out_dir / "config_used.yaml").is_file()
    assert (out_dir / "seed.txt").is_file()
    assert "Run complete" in completed.stdout
