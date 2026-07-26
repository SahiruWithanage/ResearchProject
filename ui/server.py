"""Flask backend: the UI's window onto the simulator.

Thin by design — every endpoint delegates to the existing pipeline:
option lists come from the registries (ui.introspect), validation is the
loader's own parse_config plus an Environment build, running is
Environment.run(), and every UI run writes the standard four log files
via RunLogger so results stay CLI-compatible.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, jsonify, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import parse_config  # noqa: E402
from src.config.loader import ConfigError  # noqa: E402
from src.logging_utils.run_logger import RunLogger  # noqa: E402
from src.simulation.environment import Environment  # noqa: E402
from ui.introspect import build_schema  # noqa: E402
from ui.metrics import summarize  # noqa: E402
from ui.timeline import build_timeline  # noqa: E402

# Tests monkeypatch these two; always resolve them at call time.
CONFIG_DIR = PROJECT_ROOT / "configs"
UI_LOG_DIR = PROJECT_ROOT / "logs" / "ui"

_CONFIG_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")

app = Flask(__name__, static_folder="static", static_url_path="")

# Completed runs kept in memory for this server session (the replay tab
# reads them); the log files on disk are the durable record.
_RUNS: dict[str, dict[str, Any]] = {}
_RUNS_LOCK = threading.Lock()


def _error(message: str, status: int = 400, **extra: Any):
    payload = {"ok": False, "error": message, **extra}
    return jsonify(payload), status


def _exc_message(e: BaseException) -> str:
    # str(KeyError) wraps the message in quotes; unwrap it.
    if isinstance(e, KeyError) and e.args:
        return str(e.args[0])
    return str(e)


def _load_yaml_body() -> tuple[dict[str, Any] | None, Any]:
    """Read {"yaml": "..."} from the request; returns (raw_dict, error)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("yaml"), str):
        return None, _error("request body must be JSON with a 'yaml' string field")
    try:
        raw = yaml.safe_load(body["yaml"])
    except yaml.YAMLError as e:
        return None, _error(f"not valid YAML: {e}", stage="yaml")
    if not isinstance(raw, dict):
        return None, _error(
            f"top-level YAML must be a mapping, got {type(raw).__name__}",
            stage="yaml",
        )
    return raw, None


def _build_environment(raw: dict[str, Any]) -> tuple[Environment | None, Any]:
    """Config dict -> constructed Environment, or a staged error response."""
    try:
        config = parse_config(raw)
    except ConfigError as e:
        return None, _error(str(e), stage="config")
    try:
        # Construction instantiates every plugin: unknown types (KeyError
        # from the registry) and bad params (TypeError/ValueError from the
        # plugin __init__, FileNotFoundError for traces) surface here.
        env = Environment(config)
    except (KeyError, TypeError, ValueError, OSError) as e:
        return None, _error(_exc_message(e), stage="build")
    return env, None


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return app.send_static_file("index.html")


# ---------------------------------------------------------------------------
# Schema and configs
# ---------------------------------------------------------------------------


@app.route("/api/schema")
def schema():
    # Rebuilt per request on purpose: a plugin registered after startup
    # (or during tests) appears immediately.
    return jsonify(build_schema())


@app.route("/api/configs")
def list_configs():
    names = sorted(p.name for p in CONFIG_DIR.glob("*.yaml"))
    names += sorted(p.name for p in CONFIG_DIR.glob("*.yml"))
    return jsonify({"configs": names})


@app.route("/api/configs/<name>")
def get_config(name: str):
    if not _CONFIG_NAME.match(name):
        return _error(f"invalid config name {name!r}")
    path = CONFIG_DIR / name
    if not path.exists():
        return _error(f"no such config {name!r}", status=404)
    return jsonify({"name": name, "yaml": path.read_text(encoding="utf-8")})


@app.route("/api/configs/<name>", methods=["POST"])
def save_config(name: str):
    if not _CONFIG_NAME.match(name):
        return _error(f"invalid config name {name!r}")
    raw, err = _load_yaml_body()
    if err is not None:
        return err
    path = CONFIG_DIR / name
    existed = path.exists()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(request.get_json()["yaml"], encoding="utf-8")
    return jsonify({"ok": True, "name": name, "overwrote": existed})


# ---------------------------------------------------------------------------
# YAML bridge — the frontend never parses/serializes YAML itself, so the
# panel and the config loader can never disagree on syntax.
# ---------------------------------------------------------------------------


@app.route("/api/yaml/parse", methods=["POST"])
def yaml_parse():
    raw, err = _load_yaml_body()
    if err is not None:
        return err
    return jsonify({"ok": True, "data": raw})


@app.route("/api/yaml/dump", methods=["POST"])
def yaml_dump():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        return _error("request body must be JSON with a 'data' object field")
    text = yaml.safe_dump(
        body["data"], sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    return jsonify({"ok": True, "yaml": text})


# ---------------------------------------------------------------------------
# Validate and run
# ---------------------------------------------------------------------------


@app.route("/api/validate", methods=["POST"])
def validate():
    raw, err = _load_yaml_body()
    if err is not None:
        return err
    env, err = _build_environment(raw)
    if err is not None:
        return err
    return jsonify(
        {
            "ok": True,
            "nodes": len(env.config.nodes),
            "controllers": len(env.config.controllers),
            "scenarios": len(env.config.scenarios),
            "sim_duration": env.config.sim_duration,
            "seed": env.config.seed,
        }
    )


def _new_run_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with _RUNS_LOCK:
        run_id = stamp
        n = 2
        while run_id in _RUNS:
            run_id = f"{stamp}-{n}"
            n += 1
        _RUNS[run_id] = {"status": "running"}
    return run_id


@app.route("/api/run", methods=["POST"])
def run():
    raw, err = _load_yaml_body()
    if err is not None:
        return err

    run_id = _new_run_id()
    # UI runs log under logs/ui/<run_id>; recorded in the raw dict BEFORE
    # parsing so config_used.yaml states where its own logs live.
    if not isinstance(raw.get("logging"), dict):
        raw["logging"] = {}
    raw["logging"]["output_dir"] = f"logs/ui/{run_id}"

    env, err = _build_environment(raw)
    if err is not None:
        with _RUNS_LOCK:
            del _RUNS[run_id]
        return err

    t0 = time.perf_counter()
    result = env.run()
    wall = time.perf_counter() - t0

    log_dir = UI_LOG_DIR / run_id
    RunLogger(log_dir).write_run(result, env.config, raw)
    summary = summarize(result, wall_seconds=wall)

    with _RUNS_LOCK:
        _RUNS[run_id] = {
            "status": "done",
            "summary": summary,
            "raw_config": raw,
            "result": result,  # kept for the replay tab
            "log_dir": str(log_dir),
        }
    return jsonify(
        {"ok": True, "run_id": run_id, "summary": summary, "log_dir": str(log_dir)}
    )


@app.route("/api/run/<run_id>/timeline")
def run_timeline(run_id: str):
    with _RUNS_LOCK:
        entry = _RUNS.get(run_id)
    if entry is None or entry.get("status") != "done":
        return _error(f"no run {run_id!r} in this server session", status=404)
    return jsonify(
        {
            "ok": True,
            "run_id": run_id,
            "timeline": build_timeline(entry["result"], entry["raw_config"]),
        }
    )


@app.route("/api/runs")
def list_runs():
    with _RUNS_LOCK:
        runs = {
            rid: {"status": entry["status"], "summary": entry.get("summary")}
            for rid, entry in _RUNS.items()
        }
    return jsonify({"runs": runs})
