"""Tests for the YAML config loader and validator."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from src.config import (
    ConfigError,
    SimulationConfig,
    load_config,
    parse_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_raw() -> dict[str, Any]:
    return {
        "seed": 42,
        "sim_duration": 500.0,
        "dt": 1.0,
        "controllers": [
            {
                "id": "ctrl_main",
                "allocator": {"type": "local_first_helper_offload"},
                "manages": ["node_1", "node_2", "node_3"],
                "parent": None,
            }
        ],
        "nodes": [
            {
                "id": "node_1",
                "type": "source",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {"generator": {"type": "poisson", "rate": 0.3}},
            },
            {
                "id": "node_2",
                "type": "source",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {"generator": {"type": "poisson", "rate": 0.3}},
            },
            {
                "id": "node_3",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {
            "output_dir": "logs/phase1_run01",
            "log_state_every": 1.0,
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_config_parses_into_simulation_config(valid_raw):
    cfg = parse_config(valid_raw)
    assert isinstance(cfg, SimulationConfig)
    assert cfg.seed == 42
    assert cfg.sim_duration == 500.0
    assert cfg.dt == 1.0
    assert len(cfg.nodes) == 3
    assert len(cfg.controllers) == 1
    assert cfg.controllers[0].id == "ctrl_main"
    assert cfg.controllers[0].allocator.type == "local_first_helper_offload"
    assert cfg.controllers[0].parent is None
    assert cfg.logging.output_dir == "logs/phase1_run01"
    assert cfg.logging.log_state_every == 1.0


def test_generator_params_carry_kwargs(valid_raw):
    cfg = parse_config(valid_raw)
    gen = cfg.nodes[0].source.generator
    assert gen.type == "poisson"
    assert gen.params == {"rate": 0.3}


def test_helper_node_has_no_source(valid_raw):
    cfg = parse_config(valid_raw)
    helper = next(n for n in cfg.nodes if n.type == "helper")
    assert helper.source is None


def test_logging_log_state_every_defaults_to_one(valid_raw):
    valid_raw["logging"].pop("log_state_every")
    cfg = parse_config(valid_raw)
    assert cfg.logging.log_state_every == 1.0


def test_loads_phase1_yaml_from_disk():
    path = Path(__file__).resolve().parent.parent / "configs" / "phase1.yaml"
    cfg = load_config(path)
    assert isinstance(cfg, SimulationConfig)
    assert len(cfg.nodes) == 3
    assert cfg.controllers[0].allocator.type == "local_first_helper_offload"


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_key", ["seed", "sim_duration", "dt",
                                          "controllers", "nodes", "logging"])
def test_missing_top_level_field_raises(valid_raw, missing_key):
    valid_raw.pop(missing_key)
    with pytest.raises(ConfigError, match=f"missing required field '{missing_key}'"):
        parse_config(valid_raw)


def test_missing_node_field_raises(valid_raw):
    valid_raw["nodes"][0].pop("cpu_capacity")
    with pytest.raises(ConfigError, match="missing required field 'cpu_capacity'"):
        parse_config(valid_raw)


def test_missing_controller_allocator_raises(valid_raw):
    valid_raw["controllers"][0].pop("allocator")
    with pytest.raises(ConfigError, match="missing required field 'allocator'"):
        parse_config(valid_raw)


def test_missing_generator_type_raises(valid_raw):
    valid_raw["nodes"][0]["source"]["generator"].pop("type")
    with pytest.raises(ConfigError, match="missing required field 'type'"):
        parse_config(valid_raw)


# ---------------------------------------------------------------------------
# Wrong types
# ---------------------------------------------------------------------------

def test_seed_must_be_int_not_string(valid_raw):
    valid_raw["seed"] = "not-an-int"
    with pytest.raises(ConfigError, match="seed.*must be int"):
        parse_config(valid_raw)


def test_seed_must_be_int_not_bool(valid_raw):
    valid_raw["seed"] = True
    with pytest.raises(ConfigError, match="seed.*must be int"):
        parse_config(valid_raw)


def test_sim_duration_must_be_a_number(valid_raw):
    valid_raw["sim_duration"] = "long"
    with pytest.raises(ConfigError, match="sim_duration.*must be a number"):
        parse_config(valid_raw)


def test_nodes_must_be_a_list(valid_raw):
    valid_raw["nodes"] = "not-a-list"
    with pytest.raises(ConfigError, match="nodes.*must be a list"):
        parse_config(valid_raw)


# ---------------------------------------------------------------------------
# Value constraints
# ---------------------------------------------------------------------------

def test_sim_duration_must_be_positive(valid_raw):
    valid_raw["sim_duration"] = 0
    with pytest.raises(ConfigError, match="sim_duration must be > 0"):
        parse_config(valid_raw)


def test_dt_must_be_positive(valid_raw):
    valid_raw["dt"] = -0.1
    with pytest.raises(ConfigError, match="dt must be > 0"):
        parse_config(valid_raw)


def test_at_least_two_nodes_required(valid_raw):
    valid_raw["nodes"] = valid_raw["nodes"][:1]
    valid_raw["controllers"][0]["manages"] = ["node_1"]
    with pytest.raises(ConfigError, match="nodes must have at least 2 entries"):
        parse_config(valid_raw)


def test_at_least_one_controller_required(valid_raw):
    valid_raw["controllers"] = []
    with pytest.raises(ConfigError, match="controllers must have at least 1 entry"):
        parse_config(valid_raw)


def test_node_type_must_be_valid(valid_raw):
    valid_raw["nodes"][0]["type"] = "garbage"
    with pytest.raises(ConfigError, match="type must be one of"):
        parse_config(valid_raw)


def test_node_capacity_must_be_positive(valid_raw):
    valid_raw["nodes"][0]["cpu_capacity"] = 0
    with pytest.raises(ConfigError, match="cpu_capacity must be > 0"):
        parse_config(valid_raw)


def test_log_state_every_must_be_positive(valid_raw):
    valid_raw["logging"]["log_state_every"] = 0
    with pytest.raises(ConfigError, match="log_state_every must be > 0"):
        parse_config(valid_raw)


# ---------------------------------------------------------------------------
# Source / helper block consistency
# ---------------------------------------------------------------------------

def test_source_node_without_source_block_raises(valid_raw):
    del valid_raw["nodes"][0]["source"]
    with pytest.raises(ConfigError, match="is missing a 'source' block"):
        parse_config(valid_raw)


def test_helper_node_with_source_block_raises(valid_raw):
    valid_raw["nodes"][2]["source"] = {
        "generator": {"type": "poisson", "rate": 0.1}
    }
    with pytest.raises(ConfigError, match="helper nodes must not have sources"):
        parse_config(valid_raw)


# ---------------------------------------------------------------------------
# ID uniqueness
# ---------------------------------------------------------------------------

def test_duplicate_node_ids_raise(valid_raw):
    valid_raw["nodes"][1]["id"] = "node_1"
    with pytest.raises(ConfigError, match="duplicate node id"):
        parse_config(valid_raw)


def test_duplicate_controller_ids_raise(valid_raw):
    second = copy.deepcopy(valid_raw["controllers"][0])
    second["manages"] = []
    valid_raw["controllers"].append(second)
    with pytest.raises(ConfigError, match="duplicate controller id"):
        parse_config(valid_raw)


# ---------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------

def test_manages_unknown_node_raises(valid_raw):
    valid_raw["controllers"][0]["manages"] = ["node_1", "node_2", "ghost"]
    with pytest.raises(ConfigError, match="manages unknown node 'ghost'"):
        parse_config(valid_raw)


def test_node_managed_by_multiple_controllers_raises(valid_raw):
    second = {
        "id": "ctrl_other",
        "allocator": {"type": "local_first_helper_offload"},
        "manages": ["node_1"],
        "parent": None,
    }
    valid_raw["controllers"].append(second)
    with pytest.raises(ConfigError, match="managed by multiple controllers"):
        parse_config(valid_raw)


def test_unmanaged_node_raises(valid_raw):
    valid_raw["controllers"][0]["manages"] = ["node_1", "node_2"]
    with pytest.raises(ConfigError, match="not managed by any controller"):
        parse_config(valid_raw)


def test_unknown_parent_controller_raises(valid_raw):
    valid_raw["controllers"][0]["parent"] = "ctrl_phantom"
    with pytest.raises(ConfigError, match="parent 'ctrl_phantom'"):
        parse_config(valid_raw)


def test_self_parent_raises(valid_raw):
    valid_raw["controllers"][0]["parent"] = "ctrl_main"
    with pytest.raises(ConfigError, match="cannot be its own parent"):
        parse_config(valid_raw)


# ---------------------------------------------------------------------------
# File-level errors
# ---------------------------------------------------------------------------

def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "nope.yaml")


def test_empty_file_raises(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    with pytest.raises(ConfigError, match="is empty"):
        load_config(f)


def test_non_mapping_top_level_raises(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(f)


# ---------------------------------------------------------------------------
# Sandbox-like flexibility
# ---------------------------------------------------------------------------

def test_adding_more_nodes_works_without_code_changes(valid_raw):
    extra_nodes = [
        {
            "id": "node_4",
            "type": "source",
            "cpu_capacity": 2.0,
            "memory_capacity": 4.0,
            "tier": "edge",
            "source": {"generator": {"type": "poisson", "rate": 0.1}},
        },
        {
            "id": "node_5",
            "type": "helper",
            "cpu_capacity": 8.0,
            "memory_capacity": 16.0,
            "tier": "edge",
        },
    ]
    valid_raw["nodes"].extend(extra_nodes)
    valid_raw["controllers"][0]["manages"] += ["node_4", "node_5"]
    cfg = parse_config(valid_raw)
    assert len(cfg.nodes) == 5
