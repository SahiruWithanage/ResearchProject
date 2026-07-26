"""Evidence runs for the report: validation, allocator comparison, staleness.

Everything runs on configs/heterogeneous.yaml (the full-realism showcase:
heterogeneous node profiles, task mixes, sinusoidal load, jitter, downlink,
heartbeat observability, scheduling delay) with only the variable under
study changed between runs.
"""

from __future__ import annotations

import statistics
import sys
from copy import deepcopy
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import parse_config  # noqa: E402
from src.simulation import Environment  # noqa: E402

BASE = yaml.safe_load((PROJECT_ROOT / "configs" / "heterogeneous.yaml").read_text())


def run(raw: dict) -> dict:
    result = Environment(parse_config(raw)).run()
    outcomes = result.outcomes
    done = [o for o in outcomes if o.actual_completion_time is not None]
    met = [o for o in done if o.deadline_met]
    lost = [o for o in outcomes if o.task_lost]
    latency = [
        (o.return_end or o.actual_completion_time) - o.decision_time for o in done
    ]
    placement: dict[str, int] = {}
    for o in outcomes:
        if o.selected_node:
            placement[o.selected_node] = placement.get(o.selected_node, 0) + 1
    return {
        "tasks": len(outcomes),
        "done": len(done),
        "met_pct": 100.0 * len(met) / len(done) if done else 0.0,
        "lost": len(lost),
        "mean_latency": statistics.mean(latency) if latency else 0.0,
        "placement": placement,
    }


# ---------------------------------------------------------------------------
# A. M/D/1 validation numbers (steps 3-4 evidence)
# ---------------------------------------------------------------------------
print("=" * 74)
print("A. Queueing-theory validation: M/D/1, lambda=0.5, S=1.0 s  (theory: 1.5 s)")
print("=" * 74)
mdone_raw = {
    "seed": 0,
    "sim_duration": 2000.0,
    "dt": 0.01,
    "controllers": [
        {
            "id": "c",
            "allocator": {
                "type": "local_first_helper_offload",
                "max_local_queue": 10**9,
            },
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
                    "rate": 0.5,
                    "cpu_demand": 1.0,
                    "deadline_offset": 500.0,
                }
            },
        },
        {
            "id": "node_h",
            "type": "helper",
            "cpu_capacity": 1.0,
            "memory_capacity": 8.0,
            "tier": "edge",
        },
    ],
    "logging": {"output_dir": "logs/x", "log_state_every": 100.0},
}
for seed in (724, 725, 726):
    raw = deepcopy(mdone_raw)
    raw["seed"] = seed
    result = Environment(parse_config(raw)).run()
    soj = [
        o.actual_completion_time - o.decision_time
        for o in result.outcomes
        if o.actual_completion_time is not None and o.decision_time > 200.0
    ]
    print(
        f"  seed {seed}: n={len(soj):4d} tasks   "
        f"measured mean sojourn = {statistics.mean(soj):.4f} s"
    )

# ---------------------------------------------------------------------------
# B. Allocator comparison (steps 5-9 evidence): identical world, same seed,
#    only the allocation strategy differs.
# ---------------------------------------------------------------------------
print()
print("=" * 74)
print(
    f"B. Allocator comparison on configs/heterogeneous.yaml (seed {BASE['seed']}, 300 s)"
)
print("   Heterogeneous profiles + task mix + sinusoidal load + jitter +")
print("   downlink + heartbeat(1s)/sched-delay(20ms).")
print("=" * 74)
allocators = [
    ("local_first_helper_offload", {"type": "local_first_helper_offload"}),
    ("load_aware", {"type": "load_aware"}),
    ("latency_first", {"type": "latency_first"}),
    ("weighted_score (1,1,1,0)", {"type": "weighted_score"}),
    ("weighted_score energy", {"type": "weighted_score", "w_energy": 2.0}),
]
header = (
    f"  {'allocator':28} {'done':>9} {'deadline%':>10} {'mean lat s':>11}  placement"
)
print(header)
print("  " + "-" * (len(header) - 2))
for name, alloc in allocators:
    raw = deepcopy(BASE)
    raw["controllers"][0]["allocator"] = alloc
    r = run(raw)
    placement = ", ".join(f"{k}:{v}" for k, v in sorted(r["placement"].items()))
    print(
        f"  {name:28} {r['done']:>4}/{r['tasks']:<4} {r['met_pct']:>9.1f}% "
        f"{r['mean_latency']:>10.2f}   {placement}"
    )

# ---------------------------------------------------------------------------
# C. Observability comparison (step 8 evidence): the SAME allocator with
#    fresher or staler knowledge.
# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("C. Value of information: load_aware under different observability")
print("   (same seed, same workload - only the controller's knowledge differs)")
print("=" * 74)
observabilities = [
    ("perfect (live truth)", None),
    ("heartbeat 1 s", {"type": "heartbeat", "interval": 1.0, "report_delay": 0.02}),
    ("heartbeat 5 s", {"type": "heartbeat", "interval": 5.0, "report_delay": 0.02}),
    ("heartbeat 15 s", {"type": "heartbeat", "interval": 15.0, "report_delay": 0.02}),
]
print(f"  {'observability':24} {'done':>9} {'deadline%':>10} {'mean lat s':>11}")
print("  " + "-" * 58)
for name, obs in observabilities:
    raw = deepcopy(BASE)
    raw["controllers"][0]["allocator"] = {"type": "load_aware"}
    if obs is None:
        raw["controllers"][0].pop("observability", None)
    else:
        raw["controllers"][0]["observability"] = obs
    r = run(raw)
    print(
        f"  {name:24} {r['done']:>4}/{r['tasks']:<4} {r['met_pct']:>9.1f}% "
        f"{r['mean_latency']:>10.2f}"
    )
