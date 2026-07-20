"""Environment: builds the simulation from config, runs the tick loop, returns results."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import src.network  # noqa: F401 — register network models
from src.config import (
    SimulationConfig,
    allocators,
    generators,
    network_models,
    observability_models,
)
from src.controller.controller import Controller
from src.generation.base import TaskGenerator
from src.models import AllocationOutcome, EdgeNode, NodeState, Task
from src.simulation.clock import Clock
from src.simulation.estimates import CompletionEstimator
from src.simulation.processing import NodeRuntime
from src.config.factory import scenarios as scenario_registry
from src.simulation.scenarios import Scenario  # noqa: F401 — registers scenarios
from src.simulation.transit import TransitQueue


@dataclass
class EnvironmentResult:
    """What one run produced."""

    outcomes: list[AllocationOutcome]
    snapshots: list[NodeState]
    final_time: float


class Environment:
    """Top-level simulator."""

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self._clock = Clock(dt=config.dt)
        self._transit = TransitQueue()  # uplink: task payloads to executors
        self._return_transit = TransitQueue()  # downlink: results going home

        # One SeedSequence feeds every random stream. Sources take children
        # 0..n-1 (stable spawn keys), the network takes child n; spawning from
        # separate SeedSequence objects would hand out colliding streams.
        seed_seq = np.random.SeedSequence(config.seed)
        source_nodes = [n for n in config.nodes if n.type == "source"]
        sub_seeds = seed_seq.spawn(len(source_nodes) + 1)

        self._network = self._build_network(config, sub_seeds[len(source_nodes)])
        self._estimator = CompletionEstimator(self._network)

        self.nodes: dict[str, EdgeNode] = {}
        self.runtimes: dict[str, NodeRuntime] = {}
        for node_cfg in config.nodes:
            node = EdgeNode(
                node_id=node_cfg.id,
                node_type=node_cfg.type,
                cpu_capacity=node_cfg.cpu_capacity,
                memory_capacity=node_cfg.memory_capacity,
                tier=node_cfg.tier,
                cpu_speed=node_cfg.cpu_speed,
                queue_limit=node_cfg.queue_limit,
                accepts_task_types=node_cfg.accepts_task_types,
                gpu_capacity=node_cfg.gpu_capacity,
                energy_cost_factor=node_cfg.energy_cost_factor,
            )
            self.nodes[node.node_id] = node
            self.runtimes[node.node_id] = NodeRuntime(node)

        self.generators: dict[str, TaskGenerator] = {}
        for node_cfg, sub_seed in zip(source_nodes, sub_seeds):
            gen_cls = generators.get(node_cfg.source.generator.type)
            rng = np.random.default_rng(sub_seed)
            self.generators[node_cfg.id] = gen_cls(
                **node_cfg.source.generator.params,
                source_node_id=node_cfg.id,
                rng=rng,
            )

        self.controllers: dict[str, Controller] = {}
        self._controller_by_node: dict[str, Controller] = {}
        for ctrl_cfg in config.controllers:
            alloc_cls = allocators.get(ctrl_cfg.allocator.type)
            alloc = alloc_cls(**ctrl_cfg.allocator.params)
            obs_cls = observability_models.get(ctrl_cfg.observability.type)
            observability = obs_cls(**ctrl_cfg.observability.params)
            managed_runtimes = [self.runtimes[node_id] for node_id in ctrl_cfg.manages]
            ctrl = Controller(
                id=ctrl_cfg.id,
                allocator=alloc,
                allocator_type=ctrl_cfg.allocator.type,
                managed_nodes=managed_runtimes,
                parent_id=ctrl_cfg.parent,
                observability=observability,
                scheduling_delay=ctrl_cfg.scheduling_delay,
            )
            self.controllers[ctrl.id] = ctrl
            for runtime in managed_runtimes:
                self._controller_by_node[runtime.node_id] = ctrl

        self._scenarios: list[Scenario] = []
        for sc_cfg in config.scenarios:
            sc_cls = scenario_registry.get(sc_cfg.type)
            self._scenarios.append(sc_cls(**sc_cfg.params))

    def _build_network(
        self, config: SimulationConfig, network_seed: np.random.SeedSequence
    ):
        net_cfg = config.network
        net_cls = network_models.get(net_cfg.type)
        kwargs = dict(net_cfg.params)
        if net_cfg.type in ("fluid_link", "varying_fluid_link"):
            kwargs.update(
                default_profile=net_cfg.default_profile,
                profiles=net_cfg.profiles,
                links=net_cfg.links,
                rng=np.random.default_rng(network_seed),
            )
        if net_cfg.type == "varying_fluid_link":
            # Stable integer entropy for the counter-based quality factors;
            # generate_state is a pure function (consumes nothing).
            kwargs.setdefault(
                "variation_entropy", int(network_seed.generate_state(2)[1])
            )
        return net_cls(**kwargs)

    def run(self) -> EnvironmentResult:
        snapshots: list[NodeState] = []
        snapshots.extend(rt.snapshot(0.0) for rt in self.runtimes.values())
        next_snapshot_t = self.config.logging.log_state_every

        while self._clock.t < self.config.sim_duration:
            t_start = self._clock.t
            t_end = min(t_start + self._clock.dt, self.config.sim_duration)
            actual_dt = t_end - t_start

            # Instability first: failures/recoveries take effect for this tick.
            for scenario in self._scenarios:
                scenario.tick(t_start, self)

            for runtime in self.runtimes.values():
                completed = runtime.advance(actual_dt, t_start)
                for finished_task, completion_time in completed:
                    self._finish_compute(runtime.node_id, finished_task, completion_time)

            self._deliver_transit(t_end)
            self._deliver_returns(t_end)

            # Heartbeats fire while the world is current, before decisions.
            for ctrl in self.controllers.values():
                ctrl.observability.refresh(t_end)

            new_tasks: list[Task] = []
            for gen in self.generators.values():
                new_tasks.extend(gen.emit(t_start, t_end))
            new_tasks.sort(key=lambda t: (t.arrival_time, t.task_id))

            for task in new_tasks:
                ctrl = self._controller_for_task(task)
                outcome = ctrl.submit(task, t_end, self._estimator)
                self._dispatch(task, outcome)

            while next_snapshot_t <= t_end + 1e-12:
                for runtime in self.runtimes.values():
                    snapshots.append(runtime.snapshot(next_snapshot_t))
                next_snapshot_t += self.config.logging.log_state_every

            self._clock.t = t_end

        outcomes: list[AllocationOutcome] = []
        for ctrl in self.controllers.values():
            outcomes.extend(ctrl.outcomes.values())

        return EnvironmentResult(
            outcomes=outcomes,
            snapshots=snapshots,
            final_time=self._clock.t,
        )

    def _dispatch(self, task: Task, outcome: AllocationOutcome) -> None:
        if outcome.task_lost:
            return  # dropped at decision time: no enqueue, no transit

        target = outcome.selected_node
        source = task.source_node_id
        # transfer_start already includes the controller's scheduling delay.
        start = outcome.transfer_start

        if source is None or source == target:
            outcome.transfer_end = start
            outcome.compute_start = start
            if start <= outcome.decision_time:
                self.runtimes[target].enqueue(task)  # no delay: enqueue now
            else:
                self._transit.schedule(task, target, start)
            return

        delay = self._network.uplink_delay(source, target, task, start)
        arrive_at = start + delay
        outcome.transfer_end = arrive_at
        self._transit.schedule(task, target, arrive_at)

    def _finish_compute(
        self, executor_id: str, task: Task, completion_time: float
    ) -> None:
        """Record a compute completion; start the result's trip home if any.

        The deadline is judged at compute completion unless a result payload
        must travel back (remote executor and ``result_size`` > 0) — then
        judgement waits for the downlink arrival (:meth:`_deliver_returns`).
        """
        owner = self._controller_for_task(task)
        source = task.source_node_id
        needs_return = (
            task.result_size > 0.0 and source is not None and source != executor_id
        )
        owner.record_completion(task, completion_time, awaiting_return=needs_return)
        if needs_return:
            delay = self._network.downlink_delay(
                executor_id, source, task, completion_time
            )
            self._return_transit.schedule(task, source, completion_time + delay)

    # ------------------------------------------------------------------
    # Scenario world hooks (see src/simulation/scenarios.py)
    # ------------------------------------------------------------------

    def fail_node(self, node_id: str) -> None:
        """Crash a node: everything it held is lost."""
        for task in self.runtimes[node_id].fail():
            self._controller_for_task(task).record_loss(task)

    def begin_node_recovery(self, node_id: str, speed_factor: float) -> None:
        self.runtimes[node_id].begin_recovery(speed_factor)

    def restore_node(self, node_id: str) -> None:
        self.runtimes[node_id].restore()

    def set_node_reliability(self, node_id: str, value: float) -> None:
        self.runtimes[node_id].reliability_score = float(value)

    # ------------------------------------------------------------------

    def _deliver_transit(self, t_end: float) -> None:
        for entry in self._transit.deliver_due(t_end):
            runtime = self.runtimes[entry.target_node_id]
            ctrl = self._controller_for_task(entry.task)
            if not runtime.is_available():
                # Arrived at a crashed node: nobody there to receive it.
                ctrl.record_loss(entry.task)
                continue
            runtime.enqueue(entry.task)
            outcome = ctrl.outcomes[entry.task.task_id]
            outcome.compute_start = entry.arrive_at

    def _deliver_returns(self, t_end: float) -> None:
        for entry in self._return_transit.deliver_due(t_end):
            ctrl = self._controller_for_task(entry.task)
            ctrl.record_return(entry.task, entry.arrive_at)

    def _controller_for_task(self, task: Task) -> Controller:
        source = task.source_node_id
        if source is None:
            raise RuntimeError(f"task {task.task_id!r} has no source_node_id")
        ctrl = self._controller_by_node.get(source)
        if ctrl is None:
            raise RuntimeError(
                f"task {task.task_id!r} has source_node_id {source!r} "
                f"which is not managed by any controller"
            )
        return ctrl
