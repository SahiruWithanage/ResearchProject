"""Backend tests for the UI layer: schema introspection, validate, run.

The headline guarantee tested here: the UI's form schema mirrors the
plug-and-play registries automatically, so registering a new plugin makes
it appear in the UI with its parameters — no UI edits.
"""

from __future__ import annotations

import yaml
import pytest

from src.config.factory import allocators
from ui import server as ui_server
from ui.introspect import build_schema
from ui.metrics import summarize

TINY_YAML = """
seed: 1
sim_duration: 5.0
dt: 0.1
controllers:
  - id: c
    allocator: {type: load_aware}
    manages: [s, h]
    parent: null
nodes:
  - id: s
    type: source
    cpu_capacity: 1.0
    memory_capacity: 4.0
    tier: edge
    source:
      generator: {type: fixed_interval, interval: 1.0}
  - id: h
    type: helper
    cpu_capacity: 2.0
    memory_capacity: 4.0
    tier: edge
logging: {output_dir: logs/tiny_ui_test, log_state_every: 1.0}
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, "UI_LOG_DIR", tmp_path / "logs" / "ui")
    ui_server.app.config["TESTING"] = True
    with ui_server.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


def test_schema_covers_all_seven_registries():
    schema = build_schema()
    assert set(schema["registries"]) == {
        "generators",
        "allocators",
        "network_models",
        "distributions",
        "rate_patterns",
        "observability_models",
        "scenarios",
    }
    assert "weighted_score" in schema["registries"]["allocators"]
    assert "heartbeat" in schema["registries"]["observability_models"]
    assert "percentile" in schema["registries"]["distributions"]
    assert "sinusoidal" in schema["registries"]["rate_patterns"]
    assert "node_failure" in schema["registries"]["scenarios"]


def test_schema_reads_params_from_signatures():
    schema = build_schema()
    ws = schema["registries"]["allocators"]["weighted_score"]["params"]
    assert ws["w_delay"] == {"type": "number", "required": False, "default": 1.0}
    hb = schema["registries"]["observability_models"]["heartbeat"]["params"]
    assert hb["interval"]["required"] is True
    assert hb["report_delay"]["default"] == 0.0


def test_schema_hides_environment_injected_params():
    schema = build_schema()
    poisson = schema["registries"]["generators"]["poisson"]["params"]
    assert "rng" not in poisson
    assert "source_node_id" not in poisson
    assert "rate" in poisson and "task_mix" in poisson
    varying = schema["registries"]["network_models"]["varying_fluid_link"]["params"]
    assert "variation_entropy" not in varying
    assert "rng" not in varying
    assert "variation_period_s" in varying
    trace = schema["registries"]["network_models"]["trace_fluid_link"]["params"]
    assert "traces" in trace


def test_new_plugin_appears_in_schema_automatically():
    @allocators.register("test_dummy_ui_alloc")
    class DummyAllocator:
        """Dummy allocator proving the UI schema mirrors the registry."""

        def __init__(self, *, knob: float = 2.5) -> None:
            self.knob = knob

    try:
        entry = build_schema()["registries"]["allocators"]["test_dummy_ui_alloc"]
        assert entry["params"]["knob"]["default"] == 2.5
        assert entry["params"]["knob"]["type"] == "number"
        assert entry["doc"].startswith("Dummy allocator")
    finally:
        del allocators._items["test_dummy_ui_alloc"]


def test_schema_composites_are_introspected():
    comp = build_schema()["composites"]
    mix = comp["generators"]["*"]["task_mix"]
    assert mix["weight"]["required"] is True
    assert "cpu_demand" in mix and "task_type" in mix
    assert "source_node_id" not in mix  # environment-injected, hidden
    traces = comp["network_models"]["trace_fluid_link"]["traces"]
    assert traces["from"]["type"] == "node_id"
    assert "file" in traces and "min_bandwidth_bps" in traces


def test_schema_is_json_safe():
    # float("inf") in the instant profile must not leak into the payload.
    import json

    payload = json.dumps(build_schema())
    assert "Infinity" not in payload


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


def test_configs_round_trip(client, tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, "CONFIG_DIR", tmp_path / "configs")
    resp = client.post("/api/configs/tiny.yaml", json={"yaml": TINY_YAML})
    assert resp.status_code == 200
    assert resp.get_json()["overwrote"] is False

    listing = client.get("/api/configs").get_json()["configs"]
    assert listing == ["tiny.yaml"]

    fetched = client.get("/api/configs/tiny.yaml").get_json()
    assert yaml.safe_load(fetched["yaml"])["seed"] == 1


def test_config_name_must_be_plain_yaml_filename(client):
    assert client.get("/api/configs/..%5Cevil.yaml").status_code in (400, 404)
    resp = client.post("/api/configs/evil.txt", json={"yaml": TINY_YAML})
    assert resp.status_code == 400


def test_real_repo_configs_are_listed(client):
    listing = client.get("/api/configs").get_json()["configs"]
    assert "heterogeneous.yaml" in listing
    assert "phase1.yaml" in listing


# ---------------------------------------------------------------------------
# YAML bridge
# ---------------------------------------------------------------------------


def test_yaml_round_trip_preserves_data(client):
    text = (ui_server.PROJECT_ROOT / "configs" / "heterogeneous.yaml").read_text(
        encoding="utf-8"
    )
    parsed = client.post("/api/yaml/parse", json={"yaml": text}).get_json()
    assert parsed["ok"] is True
    dumped = client.post("/api/yaml/dump", json={"data": parsed["data"]}).get_json()
    reparsed = client.post("/api/yaml/parse", json={"yaml": dumped["yaml"]}).get_json()
    assert reparsed["data"] == parsed["data"]


def test_yaml_parse_rejects_bad_syntax(client):
    resp = client.post("/api/yaml/parse", json={"yaml": "a: [unclosed"})
    assert resp.status_code == 400
    assert resp.get_json()["stage"] == "yaml"


def test_yaml_dump_requires_object(client):
    resp = client.post("/api/yaml/dump", json={"data": "not an object"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def test_validate_accepts_good_config(client):
    resp = client.post("/api/validate", json={"yaml": TINY_YAML})
    body = resp.get_json()
    assert body["ok"] is True
    assert body["nodes"] == 2 and body["controllers"] == 1


def test_validate_reports_structural_errors(client):
    raw = yaml.safe_load(TINY_YAML)
    del raw["nodes"]
    resp = client.post("/api/validate", json={"yaml": yaml.safe_dump(raw)})
    body = resp.get_json()
    assert resp.status_code == 400
    assert body["ok"] is False and body["stage"] == "config"
    assert "nodes" in body["error"]


def test_validate_reports_unknown_plugin_with_known_names(client):
    raw = yaml.safe_load(TINY_YAML)
    raw["controllers"][0]["allocator"] = {"type": "no_such_allocator"}
    resp = client.post("/api/validate", json={"yaml": yaml.safe_dump(raw)})
    body = resp.get_json()
    assert body["ok"] is False and body["stage"] == "build"
    assert "no_such_allocator" in body["error"]
    assert "registered" in body["error"]  # the registry lists what IS valid


def test_validate_rejects_non_yaml(client):
    resp = client.post("/api/validate", json={"yaml": "a: [unclosed"})
    assert resp.get_json()["stage"] == "yaml"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def test_run_returns_summary_and_writes_standard_logs(client, tmp_path):
    resp = client.post("/api/run", json={"yaml": TINY_YAML})
    body = resp.get_json()
    assert body["ok"] is True
    summary = body["summary"]
    assert summary["tasks_generated"] == 5  # fixed_interval 1.0 over 5 s
    assert summary["tasks_completed"] > 0
    assert 0.0 <= summary["deadline_pct"] <= 100.0

    log_dir = tmp_path / "logs" / "ui" / body["run_id"]
    for name in ("allocation_log.csv", "state_log.csv", "config_used.yaml", "seed.txt"):
        assert (log_dir / name).exists()
    # config_used.yaml must reproduce the run: same seed, UI output dir.
    used = yaml.safe_load((log_dir / "config_used.yaml").read_text())
    assert used["seed"] == 1
    assert used["logging"]["output_dir"] == f"logs/ui/{body['run_id']}"

    runs = client.get("/api/runs").get_json()["runs"]
    assert runs[body["run_id"]]["status"] == "done"


def test_timeline_reconstructs_task_lifecycles(client):
    run_id = client.post("/api/run", json={"yaml": TINY_YAML}).get_json()["run_id"]
    body = client.get(f"/api/run/{run_id}/timeline").get_json()
    assert body["ok"] is True
    tl = body["timeline"]
    assert tl["duration"] == pytest.approx(5.0)
    assert {n["id"] for n in tl["nodes"]} == {"s", "h"}
    assert len(tl["tasks"]) == 5
    first = tl["tasks"][0]
    assert first["source"] == "s"  # derived from the task id
    assert first["decision"] is not None
    assert set(tl["states"]) <= {"s", "h"}
    for rows in tl["states"].values():  # snapshot rows sorted by time
        times = [r[0] for r in rows]
        assert times == sorted(times)


def test_timeline_unknown_run_is_404(client):
    assert client.get("/api/run/nope/timeline").status_code == 404


def test_run_summary_matches_metrics_definitions(client):
    from src.config import parse_config
    from src.simulation.environment import Environment

    result = Environment(parse_config(yaml.safe_load(TINY_YAML))).run()
    direct = summarize(result)
    via_api = client.post("/api/run", json={"yaml": TINY_YAML}).get_json()["summary"]
    for key in ("tasks_generated", "tasks_completed", "deadline_pct", "placement"):
        assert via_api[key] == direct[key]
