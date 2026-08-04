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
        _print_summary(result, elapsed_wall, logger, config)

    return 0


def _print_header(config_path: Path, config: SimulationConfig) -> None:
    print(f"Config: {config_path.name}")
    print(
        f"  seed={config.seed}  duration={config.sim_duration}s  "
        f"dt={config.dt}s  output={config.logging.output_dir}"
    )
    print()
    _print_experiment_setup(config)
    print()


def _print_experiment_setup(config: SimulationConfig) -> None:
    """Compact setup block for screenshots / lab notes."""
    print("Setup (what this run uses)")
    print("-" * 50)

    for ctrl in config.controllers:
        alloc = ctrl.allocator
        extra = ""
        if alloc.params:
            parts = [f"{k}={v}" for k, v in sorted(alloc.params.items())]
            extra = f"  ({', '.join(parts)})"
        print(f"  Allocator:     {alloc.type}{extra}")
        print(f"    controller:  {ctrl.id}")
        print(f"    manages:     {', '.join(ctrl.manages)}")
        if ctrl.observability.type != "perfect":
            obs_parts = ", ".join(
                f"{k}={v}" for k, v in sorted(ctrl.observability.params.items())
            )
            print(f"    observability: {ctrl.observability.type}  ({obs_parts})")
        if ctrl.scheduling_delay > 0:
            print(f"    sched delay: {ctrl.scheduling_delay}s per decision")

    print("  Task generators:")
    for node in config.nodes:
        if node.source is None:
            continue
        gen = node.source.generator
        params = ", ".join(
            f"{k}={v}" for k, v in sorted(gen.params.items())
        )
        line = f"    {node.id}: {gen.type}"
        if params:
            line += f"  ({params})"
        print(line)

    for node in config.nodes:
        if node.source is None:
            print(
                f"    {node.id}: (helper, no generator)  "
                f"cpu={node.cpu_capacity}"
            )

    if config.scenarios:
        print("  Instability scenarios:")
        for sc in config.scenarios:
            params = ", ".join(f"{k}={v}" for k, v in sorted(sc.params.items()))
            print(f"    {sc.type}  ({params})")

    net = config.network
    print(f"  Network:       {net.type}")
    if net.type == "fluid_link":
        print(
            f"    default link profile: {net.default_profile} "
            f"(built-in: lan, wifi, 5g)"
        )
        if net.links:
            print("    link overrides:")
            for link in net.links:
                prof = link.get("profile", net.default_profile)
                print(
                    f"      {link['from']} -> {link['to']}: {prof}"
                )
        else:
            print("    (no per-link overrides; all links use default profile)")
    else:
        print("    (instant = zero transmission delay)")

    print("-" * 50)


def _print_summary(
    result: EnvironmentResult,
    elapsed_wall: float,
    logger: RunLogger,
    config: SimulationConfig,
) -> None:
    outcomes = result.outcomes
    snapshots = result.snapshots
    completed = [o for o in outcomes if o.actual_completion_time is not None]
    # Success = ran to completion, result got home if one was due, and beat
    # the deadline. deadline_met encodes all three.
    succeeded = [o for o in outcomes if o.deadline_met is True]
    lost = [o for o in outcomes if o.task_lost]
    late = [o for o in outcomes if o.deadline_met is False and not o.task_lost]
    unfinished = [o for o in outcomes if o.deadline_met is None]

    print("=" * 60)
    print("Run complete")
    print("=" * 60)
    print(f"  Simulated time:   {result.final_time:.1f}s")
    print(f"  Wall-clock time:  {elapsed_wall:.3f}s")
    print()

    print(f"  Tasks generated:  {len(outcomes)}")
    if outcomes:
        rate = 100.0 * len(succeeded) / len(outcomes)
        print(f"  SUCCEEDED:        {len(succeeded)}/{len(outcomes)} ({rate:.1f}%)")
        print("                    (completed, returned to source, on time)")
        if lost:
            print(f"    lost:           {len(lost)} (dropped, or destroyed by a failure)")
        if late:
            print(f"    late:           {len(late)} (finished, but past the deadline)")
        if unfinished:
            print(f"    unfinished:     {len(unfinished)} (still in flight when the run ended)")
    print()

    if outcomes:
        per_node = Counter(
            o.selected_node for o in outcomes if o.selected_node is not None
        )
        print("  Tasks placed on each node (by allocator):")
        print("    (how many tasks were sent to run on that node)")
        for node in config.nodes:
            role = node.type
            count = per_node.get(node.id, 0)
            print(f"    {node.id} ({role}): {count}")
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
