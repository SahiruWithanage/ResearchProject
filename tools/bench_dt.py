"""Benchmark: how does tick size (dt) affect wall-clock cost and results?

Same workload at every dt: 500 simulated seconds, two overloaded sources
(Poisson 0.8/s, cpu_demand 2.0, data_size 500 KB) offloading over wifi to
one 4-worker helper, load_aware allocator. Multiple seeds per dt because
changing dt re-chunks the Poisson draws, so individual runs are not
path-comparable across dt — only averages are.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import parse_config  # noqa: E402
from src.simulation import Environment  # noqa: E402


def make_raw(seed: int, dt: float) -> dict:
    return {
        "seed": seed,
        "sim_duration": 500.0,
        "dt": dt,
        "network": {"type": "fluid_link", "default_profile": "wifi"},
        "controllers": [
            {
                "id": "ctrl",
                "allocator": {"type": "load_aware"},
                "manages": ["node_1", "node_2", "node_3"],
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
                        "rate": 0.8,
                        "cpu_demand": 2.0,
                        "data_size": 500000,
                        "deadline_offset": 10.0,
                    }
                },
            },
            {
                "id": "node_2",
                "type": "source",
                "cpu_capacity": 1.0,
                "memory_capacity": 8.0,
                "tier": "edge",
                "source": {
                    "generator": {
                        "type": "poisson",
                        "rate": 0.8,
                        "cpu_demand": 2.0,
                        "data_size": 500000,
                        "deadline_offset": 10.0,
                    }
                },
            },
            {
                "id": "node_3",
                "type": "helper",
                "cpu_capacity": 4.0,
                "memory_capacity": 8.0,
                "tier": "edge",
            },
        ],
        "logging": {"output_dir": "logs/bench_ignore", "log_state_every": 1.0},
    }


def run_once(seed: int, dt: float) -> dict:
    config = parse_config(make_raw(seed, dt))
    env = Environment(config)
    t0 = time.perf_counter()
    result = env.run()
    wall = time.perf_counter() - t0

    outcomes = result.outcomes
    completed = [o for o in outcomes if o.actual_completion_time is not None]
    met = [o for o in completed if o.deadline_met]
    remote = [
        o for o in outcomes if o.selected_node != o.task_id.rsplit("_", 1)[0]
    ]
    # Sojourn = completion - arrival. arrival is encoded in the outcome's
    # decision_time only indirectly; recover arrival from deadline instead:
    # deadline = arrival + 10.0 (deadline_offset above).
    return {
        "wall": wall,
        "generated": len(outcomes),
        "completed": len(completed),
        "met_pct": 100.0 * len(met) / len(completed) if completed else 0.0,
        "offload_pct": 100.0 * len(remote) / len(outcomes) if outcomes else 0.0,
    }


def sojourns(seed: int, dt: float) -> list[float]:
    """Completion - arrival for completed tasks (arrival = deadline - 10)."""
    config = parse_config(make_raw(seed, dt))
    env = Environment(config)
    # map task_id -> arrival_time by re-deriving from generators is invasive;
    # instead run and use decision/completion fields only.
    result = env.run()
    out = []
    for o in result.outcomes:
        if o.actual_completion_time is not None:
            # decision_time >= arrival; completion - decision is a lower
            # bound on sojourn that still shows dt-inflation cleanly.
            out.append(o.actual_completion_time - o.decision_time)
    return out


def main() -> None:
    plan = [
        (1.0, [724, 725, 726, 727, 728]),
        (0.1, [724, 725, 726, 727, 728]),
        (0.01, [724, 725, 726, 727, 728]),
        (0.001, [724, 725]),
    ]
    print(
        f"{'dt':>7} {'wall s/run':>11} {'tasks':>6} {'done':>6} "
        f"{'deadline%':>10} {'offload%':>9} {'mean dec->done s':>17}"
    )
    for dt, seeds in plan:
        stats = [run_once(s, dt) for s in seeds]
        soj = []
        for s in seeds[:2]:
            soj.extend(sojourns(s, dt))
        print(
            f"{dt:>7} "
            f"{statistics.mean(x['wall'] for x in stats):>11.2f} "
            f"{statistics.mean(x['generated'] for x in stats):>6.0f} "
            f"{statistics.mean(x['completed'] for x in stats):>6.0f} "
            f"{statistics.mean(x['met_pct'] for x in stats):>10.1f} "
            f"{statistics.mean(x['offload_pct'] for x in stats):>9.1f} "
            f"{statistics.mean(soj):>17.2f}"
        )


if __name__ == "__main__":
    main()
