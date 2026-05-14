"""Phase 1 simulator entry point: load config, run, write outputs, print summary.

Usage:
    python experiments/run_phase1.py configs/phase1.yaml [--seed N] [--output-dir DIR] [--quiet]

Exit codes: 0 success, 2 bad input.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

# Make `python experiments/run_phase1.py` work without installing the project.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import SimulationConfig, parse_config  # noqa: E402
from src.config.loader import ConfigError  # noqa: E402
from src.logging_utils import RunLogger  # noqa: E402
from src.simulation import Environment, EnvironmentResult  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_phase1",
        description=(
            "Run the Phase 1 edge-computing allocation simulation. "
            "Loads a YAML config, runs the simulator, and writes the run "
            "outputs (CSVs, resolved config, seed) to a directory."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "config",
        help="Path to the YAML config file (e.g. configs/phase1.yaml).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override the master seed from the config.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Override the logging.output_dir from the config.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout summary (still writes log files).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns exit code 0 on success, 2 on bad input."""
    args = build_parser().parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        print(
            f"error: config file not found: {config_path}", file=sys.stderr
        )
        return 2

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        raw_config: dict[str, Any] = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as e:
        print(f"error: failed to parse YAML in {config_path}: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: failed to read {config_path}: {e}", file=sys.stderr)
        return 2

    if not isinstance(raw_config, dict):
        print(
            f"error: top-level YAML in {config_path} must be a mapping, "
            f"got {type(raw_config).__name__}",
            file=sys.stderr,
        )
        return 2

    if args.seed is not None:
        raw_config["seed"] = args.seed
    if args.output_dir is not None:
        raw_config.setdefault("logging", {})["output_dir"] = args.output_dir

    try:
        config = parse_config(raw_config)
    except ConfigError as e:
        print(f"error: invalid config: {e}", file=sys.stderr)
        return 2

    if not args.quiet:
        _print_header(config_path, config)

    t0 = time.perf_counter()
    env = Environment(config)
    result = env.run()
    elapsed_wall = time.perf_counter() - t0

    logger = RunLogger(config.logging.output_dir)
    logger.write_run(result, config, raw_config)

    if not args.quiet:
        _print_summary(result, elapsed_wall, logger)

    return 0


def _print_header(config_path: Path, config: SimulationConfig) -> None:
    print(f"Loading config: {config_path}")
    print(
        f"  seed={config.seed}  "
        f"sim_duration={config.sim_duration}s  "
        f"dt={config.dt}s"
    )
    print(
        f"  {len(config.nodes)} node(s), "
        f"{len(config.controllers)} controller(s)"
    )
    print(f"  output_dir={config.logging.output_dir}")
    print()


def _print_summary(
    result: EnvironmentResult,
    elapsed_wall: float,
    logger: RunLogger,
) -> None:
    outcomes = result.outcomes
    snapshots = result.snapshots
    completed = [o for o in outcomes if o.actual_completion_time is not None]
    met = [o for o in completed if o.deadline_met]

    print("=" * 60)
    print("Run complete")
    print("=" * 60)
    print(f"  Simulated time:   {result.final_time:.1f}s")
    print(f"  Wall-clock time:  {elapsed_wall:.3f}s")
    print()

    print(f"  Tasks generated:  {len(outcomes)}")
    print(f"  Tasks completed:  {len(completed)}/{len(outcomes)}")
    if completed:
        rate = 100.0 * len(met) / len(completed)
        print(f"  Deadlines met:    {len(met)}/{len(completed)} ({rate:.1f}%)")
    print()

    if outcomes:
        per_node = Counter(o.selected_node for o in outcomes)
        print("  Allocations per node:")
        for node_id in sorted(per_node):
            print(f"    {node_id}: {per_node[node_id]}")
        print()

    if snapshots:
        per_node_max: dict[str, int] = {}
        for s in snapshots:
            cur = per_node_max.get(s.node_id, 0)
            if s.queue_length > cur:
                per_node_max[s.node_id] = s.queue_length
        print("  Max queue length per node:")
        for node_id in sorted(per_node_max):
            print(f"    {node_id}: {per_node_max[node_id]}")
        print()

    print(f"  Output written to: {logger.output_dir}")
    print(f"    {logger.allocation_log_path.name}  ({_size(logger.allocation_log_path)})")
    print(f"    {logger.state_log_path.name}       ({_size(logger.state_log_path)})")
    print(f"    {logger.config_path.name}    ({_size(logger.config_path)})")
    print(f"    {logger.seed_path.name}             ({_size(logger.seed_path)})")


def _size(path: Path) -> str:
    if not path.is_file():
        return "missing"
    n = path.stat().st_size
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} bytes"


if __name__ == "__main__":
    raise SystemExit(main())
