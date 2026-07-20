# Edge Computing Allocation - Honours Thesis

This is the codebase for my Honours thesis, **"Probabilistic Stability-Aware Resource Allocation in Heterogeneous Edge Computing."** The repo will keep growing as I work through the staged plan.

## What's the research about?

Edge computing is when lots of small devices (sensors, phones, small servers near users) share computing work between themselves instead of sending everything to a big central cloud. When a new piece of work arrives, *someone* has to decide which device should handle it.

The usual answers (send it to the least-busy device, or the closest one) only look at the current state. But things change - devices get busy, slow down, sometimes fail. My thesis asks: can a **Bayesian** approach, one that reasons about how *stable* each device is likely to be over the next few seconds, make better placement choices than the standard rules?

To test that, I'm building a Python simulator where I can plug in different allocation strategies and compare them. The Bayesian allocator gets compared against deterministic baselines (and eventually a "best possible" optimum) under both stable and unstable conditions.

## Where the project is right now

The thesis follows a staged plan. The repo updates as I work through each stage.

- [x] **Stages 1–2** - system model and a working simulation scaffold.
- [x] **Stage 4** - heterogeneous nodes *(current state)*: per-node `cpu_speed`, enforced memory, `queue_limit` (overflow tasks can be lost + logged), task-type suitability, and reusable `node_profiles` in YAML. GPU processing and reliability behaviour deferred (fields exist). Example: `configs/heterogeneous.yaml`.
- [x] **Delay / transmission substage** - fluid link profiles (LAN/Wi-Fi/5G) with jitter on by default, uplink *and* downlink (results travel home; deadlines count the return trip via `result_size`), CPU/transmission split, and a `varying_fluid_link` model whose link quality drifts over time.
- [x] **Task realism substage** - per-task property distributions (`{dist: uniform|normal|lognormal|exponential, ...}`), weighted task-type mixes (`task_mix`), and time-varying arrival rates (sinusoidal load curves, piecewise bursts).
- [x] **Control-plane substage** - the controller no longer has to be all-knowing: `observability: {type: heartbeat, interval, report_delay}` makes it decide from stale node reports, and `scheduling_delay` makes each decision cost time.
- [x] **Stage 5** - deterministic baselines: `load_aware`, `latency_first`, and `weighted_score` (tunable `w_delay`/`w_load`/`w_compute`/`w_energy` weights over expected delay, queue wait, service time, and energy cost).
- [ ] **Stage 6** - find the best-possible allocation as a reference point (using MILP).
- [x] **Stage 7** - trace-informed inputs: real workload rhythms + duration/memory percentiles (Azure Functions 2019) and real measured 5G link bandwidth (UCC Ireland) flow in through `trace`/`empirical`/`percentile`/`trace_fluid_link` plug-ins. Converters in `tools/`; example: `configs/trace_driven.yaml` (needs the local datasets, see comments in the file).
- [x] **Stage 8** - instability scenarios: a `scenarios:` list scripts node failures (tasks lost, heartbeats go silent, recovery at reduced speed) and reliability decay; plus the `reliability_threshold` allocator. All five stability-risk factors from the methodology are now available. Example: `configs/instability.yaml`.
- [ ] **Stage 9** - the Bayesian allocator (the actual research contribution).
- [ ] **Stage 10** - comparative experiments and analysis.

The scaffold allocator (`local_first_helper_offload`) proves the pipeline works. **Baseline allocators** `load_aware` and `latency_first` are implemented; see `resources/DELAY_MODEL.md` for the delay model. Add a `network:` block (`instant` or `fluid_link` with LAN/Wi-Fi/5G profiles) so remote tasks incur uplink delay. Example: `configs/load_test.yaml`.

## Running it

Needs Python 3.11 or newer (I'm using 3.14).

```bash
python -m venv .venv
source .venv/bin/activate           # macOS / Linux
# .\.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install -r requirements.txt
```

Run the default Phase 1 experiment:

```bash
python experiments/run_phase1.py configs/phase1.yaml
```

It writes these files into `logs/phase1_run01/`:

- `allocation_log.csv` - one row per task: where it went, when it finished, did it meet its deadline.
- `state_log.csv` - node queue lengths and how busy each node was over time.
- `config_used.yaml` and `seed.txt` - so the exact same run can be reproduced later.

### Optional flags

A few things can be overridden from the command line without editing the config:

```bash
python experiments/run_phase1.py configs/phase1.yaml --seed 7 --output-dir logs/my_run --quiet
```

- `--seed N` - use a different random seed for this run.
- `--output-dir DIR` - write output files somewhere else.
- `--quiet` - skip the summary print (files are still written).

### Editing the config

`configs/phase1.yaml` is where the experiment settings live. Things you can change without touching any Python:

- `seed` - controls all randomness. Same seed = same output every time.
- `sim_duration` - how many simulated seconds the run goes for.
- `dt` - size of one simulator tick, in seconds. Use `0.01` for real experiments (fine enough that network delays aren't distorted by tick rounding); `1.0` is fine for quick smoke tests with the `instant` network.
- `nodes` - the list of compute nodes. Each has a type (`source` or `helper`), a CPU and memory capacity, and a tier label. You can add as many as you want, the simulator handles it. Optional heterogeneity knobs per node: `cpu_speed` (0.5 = half-speed device), `queue_limit` (full nodes are skipped; if nowhere has room the task is dropped and logged as lost), `accepts_task_types` (task suitability), `gpu_capacity`, `energy_cost_factor`.
- `node_profiles` - optional named presets (like `sensor_class` or `edge_server`) so several nodes can share one spec via `profile: <name>`; per-node fields override the profile. See `configs/heterogeneous.yaml`.
- For source nodes, the `source.generator` block picks the arrival pattern (currently `poisson` or `fixed_interval`) and its parameters. Any numeric task property (`cpu_demand`, `data_size`, ...) can be a plain number or a distribution like `{dist: uniform, low: 1, high: 3}`; a `task_mix` list gives weighted task-type profiles; and `rate` can be a number or a pattern like `{pattern: sinusoidal, base: 0.5, amplitude: 0.4, period: 120}` (or `piecewise` for bursts). See `configs/heterogeneous.yaml`.
- `controllers` - which controller is in charge of which nodes, and which allocator it uses. The allocator's own settings (like `max_local_queue` for the scaffold rule) go inside its block. Optional: `observability` (`perfect` or `heartbeat` with `interval`/`report_delay` - how fresh the controller's view of node state is) and `scheduling_delay` (seconds each decision takes before dispatch).
- `logging` - where output files go and how often to snapshot node state.

### Tests

```bash
python -m pytest
```

## Inside the repo

```
src/                    # the simulator
  models/                 # core data types: Task, EdgeNode, NodeState, AllocationOutcome
  config/                 # YAML loading, validation, plugin registry
  generation/             # task generators and their interface
  controller/             # the controller and allocator strategies
  simulation/             # clock, per-node processing, top-level Environment
  logging_utils/          # writes the CSV log files

experiments/            # CLI entry points. run_phase1.py is the current one
configs/                # YAML experiment configs
tests/                  # the pytest test suite
logs/                   # simulator outputs (gitignored)
```

The two interface files (`generation/base.py` and `controller/allocators/base.py`) are the plug-in seams. Every future generator or allocator implements one of these and gets registered by name.

## A few things worth knowing

- Task generators and allocators are **swappable via the YAML config** - adding a new one is a Python class plus a one-line registration. That's how each later stage plugs in without rewriting anything.
- All randomness goes through one master `seed`. Same config + same seed = same outputs every time.
