"""Environment: builds the simulation from config, runs the tick loop, returns results."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.config import (
    SimulationConfig,
    allocators,
    generators,
)
from src.controller.controller import Controller
from src.generation.base import TaskGenerator
from src.models import AllocationOutcome, EdgeNode, NodeState, Task
from src.simulation.clock import Clock
from src.simulation.processing import NodeRuntime


@dataclass
class EnvironmentResult:
    """What one run produced: every allocation outcome, every state snapshot, and the final simulated time."""

    outcomes: list[AllocationOutcome]
    snapshots: list[NodeState]
    final_time: float


class Environment:
    """Top-level simulator. Builds nodes, generators, and controllers from a
    :class:`SimulationConfig`, then drives the fixed-step tick loop until
    ``sim_duration`` is reached.

    Each source node gets its own RNG sub-stream spawned from the master
    seed, so adding or removing sources doesn't shift other sources'
    arrival patterns.
    """

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self._clock = Clock(dt=config.dt)

        # Nodes & runtimes
        self.nodes: dict[str, EdgeNode] = {}
        self.runtimes: dict[str, NodeRuntime] = {}
        for node_cfg in config.nodes:
            node = EdgeNode(
                node_id=node_cfg.id,
                node_type=node_cfg.type,
                cpu_capacity=node_cfg.cpu_capacity,
                memory_capacity=node_cfg.memory_capacity,
                tier=node_cfg.tier,
            )
            self.nodes[node.node_id] = node
            self.runtimes[node.node_id] = NodeRuntime(node)

        # Generators: one per source, each with its own RNG sub-stream from the master seed.
        seed_seq = np.random.SeedSequence(config.seed)
        source_nodes = [n for n in config.nodes if n.type == "source"]
        sub_seeds = seed_seq.spawn(len(source_nodes))

        self.generators: dict[str, TaskGenerator] = {}
        for node_cfg, sub_seed in zip(source_nodes, sub_seeds):
            gen_cls = generators.get(node_cfg.source.generator.type)
            rng = np.random.default_rng(sub_seed)
            self.generators[node_cfg.id] = gen_cls(
                **node_cfg.source.generator.params,
                source_node_id=node_cfg.id,
                rng=rng,
            )

        # Controllers
        self.controllers: dict[str, Controller] = {}
        self._controller_by_node: dict[str, Controller] = {}
        for ctrl_cfg in config.controllers:
            alloc_cls = allocators.get(ctrl_cfg.allocator.type)
            alloc = alloc_cls(**ctrl_cfg.allocator.params)
            managed_runtimes = [self.runtimes[node_id] for node_id in ctrl_cfg.manages]
            ctrl = Controller(
                id=ctrl_cfg.id,
                allocator=alloc,
                allocator_type=ctrl_cfg.allocator.type,
                managed_nodes=managed_runtimes,
                parent_id=ctrl_cfg.parent,
            )
            self.controllers[ctrl.id] = ctrl
            for runtime in managed_runtimes:
                self._controller_by_node[runtime.node_id] = ctrl

    def run(self) -> EnvironmentResult:
        """Run the simulation until sim_duration is reached."""
        snapshots: list[NodeState] = []
        snapshots.extend(rt.snapshot(0.0) for rt in self.runtimes.values())
        next_snapshot_t = self.config.logging.log_state_every

        while self._clock.t < self.config.sim_duration:
            t_start = self._clock.t
            t_end = min(t_start + self._clock.dt, self.config.sim_duration)
            actual_dt = t_end - t_start

            # 1. Drain work from active tasks, report completions to their controllers.
            for runtime in self.runtimes.values():
                completed = runtime.advance(actual_dt, t_start)
                for finished_task, completion_time in completed:
                    owner = self._controller_for_task(finished_task)
                    owner.record_completion(finished_task, completion_time)

            # 2. Emit new tasks arriving in [t_start, t_end). They start work next tick.
            new_tasks: list[Task] = []
            for gen in self.generators.values():
                new_tasks.extend(gen.emit(t_start, t_end))
            new_tasks.sort(key=lambda t: (t.arrival_time, t.task_id))

            # 3. Allocate each task. Allocator sees the post-advance node state.
            for task in new_tasks:
                ctrl = self._controller_for_task(task)
                ctrl.submit(task, t=t_end)

            # 4. Snapshot state at every log_state_every boundary in (prev, t_end].
            while next_snapshot_t <= t_end + 1e-12:
                for runtime in self.runtimes.values():
                    snapshots.append(runtime.snapshot(next_snapshot_t))
                next_snapshot_t += self.config.logging.log_state_every

            # 5. Advance the clock.
            self._clock.t = t_end

        outcomes: list[AllocationOutcome] = []
        for ctrl in self.controllers.values():
            outcomes.extend(ctrl.outcomes.values())

        return EnvironmentResult(
            outcomes=outcomes,
            snapshots=snapshots,
            final_time=self._clock.t,
        )

    def _controller_for_task(self, task: Task) -> Controller:
        # Routing is by source_node_id: the source's controller owns the task end-to-end.
        source = task.source_node_id
        if source is None:
            raise RuntimeError(
                f"task {task.task_id!r} has no source_node_id; "
                f"the Phase 1 environment cannot route tasks without it"
            )
        ctrl = self._controller_by_node.get(source)
        if ctrl is None:
            raise RuntimeError(
                f"task {task.task_id!r} has source_node_id {source!r} "
                f"which is not managed by any controller"
            )
        return ctrl
