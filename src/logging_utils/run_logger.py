"""RunLogger: writes one run's outputs (CSVs + config + seed) to a directory."""

from __future__ import annotations
import csv
from pathlib import Path
from typing import Any
import yaml
from src.config.schema import SimulationConfig
from src.logging_utils.manifest import build_manifest, write_manifest
from src.models import AllocationOutcome, NodeState
from src.simulation.environment import EnvironmentResult

# Column orders are part of the public output format. Don't reorder casually.
# Ordered along a task's life: arrival -> decision -> transfer -> compute ->
# completion -> return.
_ALLOC_FIELDS: tuple[str, ...] = (
    "task_id",
    "source_node_id",
    "arrival_time",
    "deadline",
    "decision_time",
    "allocator_type",
    "selected_node",
    "estimated_completion_time",
    "transfer_start",
    "transfer_end",
    "compute_start",
    "actual_completion_time",
    "return_end",
    "deadline_met",
    "task_lost",
)

_STATE_FIELDS: tuple[str, ...] = (
    "time_step",
    "node_id",
    "queue_length",
    "active_tasks",
    "cpu_utilisation",
    "memory_utilisation",
    "reliability_score",
    "failure_state",
    "queued_work",
)


class RunLogger:
    """Writes a run's outputs into a directory. Existing files are overwritten."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    def write_run(
        self,
        result: EnvironmentResult,
        config: SimulationConfig,
        raw_config: dict[str, Any],
    ) -> None:
        """Write the run bundle: two CSVs, the config, the seed, the manifest."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_allocation_log(result.outcomes)
        self._write_state_log(result.snapshots)
        self._write_config(raw_config)
        self._write_seed(config.seed)
        # Last: it checksums the files written above.
        self._write_manifest(config)

    @property
    def allocation_log_path(self) -> Path:
        return self.output_dir / "allocation_log.csv"

    @property
    def state_log_path(self) -> Path:
        return self.output_dir / "state_log.csv"

    @property
    def config_path(self) -> Path:
        return self.output_dir / "config_used.yaml"

    @property
    def seed_path(self) -> Path:
        return self.output_dir / "seed.txt"

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "run_manifest.json"

    def _write_manifest(self, config: SimulationConfig) -> None:
        write_manifest(
            self.manifest_path,
            build_manifest(
                repo_root=Path(__file__).resolve().parent.parent.parent,
                seed=config.seed,
                sim_duration=config.sim_duration,
                dt=config.dt,
                allocators=sorted({c.allocator.type for c in config.controllers}),
                output_files={
                    "allocation_log.csv": self.allocation_log_path,
                    "state_log.csv": self.state_log_path,
                    "config_used.yaml": self.config_path,
                },
            ),
        )

    def _write_allocation_log(self, outcomes: list[AllocationOutcome]) -> None:
        ordered = sorted(outcomes, key=lambda o: (o.decision_time, o.task_id))
        with self.allocation_log_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(_ALLOC_FIELDS)
            for o in ordered:
                writer.writerow(
                    [
                        o.task_id,
                        o.source_node_id or "",
                        _fmt_float(o.arrival_time),
                        _fmt_float(o.deadline),
                        _fmt_float(o.decision_time),
                        o.allocator_type,
                        o.selected_node or "",
                        _fmt_float(o.estimated_completion_time),
                        _fmt_float(o.transfer_start),
                        _fmt_float(o.transfer_end),
                        _fmt_float(o.compute_start),
                        _fmt_float(o.actual_completion_time),
                        _fmt_float(o.return_end),
                        _fmt_bool(o.deadline_met),
                        _fmt_bool(o.task_lost),
                    ]
                )

    def _write_state_log(self, snapshots: list[NodeState]) -> None:
        ordered = sorted(snapshots, key=lambda s: (s.time_step, s.node_id))
        with self.state_log_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(_STATE_FIELDS)
            for s in ordered:
                writer.writerow(
                    [
                        _fmt_float(s.time_step),
                        s.node_id,
                        s.queue_length,
                        s.active_tasks,
                        _fmt_float(s.cpu_utilisation),
                        _fmt_float(s.memory_utilisation),
                        _fmt_float(s.reliability_score),
                        s.failure_state,
                        _fmt_float(s.queued_work),
                    ]
                )

    def _write_config(self, raw_config: dict[str, Any]) -> None:
        with self.config_path.open("w", encoding="utf-8", newline="") as f:
            yaml.safe_dump(
                raw_config,
                f,
                sort_keys=False,
                default_flow_style=False,
            )

    def _write_seed(self, seed: int) -> None:
        with self.seed_path.open("w", encoding="utf-8", newline="") as f:
            f.write(f"{seed}\n")


# repr(float(v)) gives the shortest round-trip decimal, stable across platforms and numpy.
def _fmt_float(v: float | None) -> str:
    if v is None:
        return ""
    return repr(float(v))


def _fmt_bool(v: bool | None) -> str:
    if v is None:
        return ""
    return "True" if v else "False"
