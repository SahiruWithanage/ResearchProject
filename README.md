# Edge Computing Allocation - Honours Thesis

Codebase for my Honours thesis, **"Probabilistic Stability-Aware Resource
Allocation in Heterogeneous Edge Computing: A Hierarchical and Distributed
Dynamic Bayesian Approach."**

## What's the research about?

Edge computing is when lots of small devices (sensors, phones, small servers
near users) share computing work between themselves instead of sending
everything to a distant cloud. When a new piece of work arrives, *someone*
has to decide which device should handle it.

The usual answers (send it to the least-busy device, or the closest one) only
look at the current state, and they assume that state stays true after the
decision is made. In real edge environments it doesn't: devices get busy,
links slow down, nodes degrade and fail, and the controller's picture of the
world is always slightly out of date. My thesis asks whether a **Bayesian**
approach, one that reasons about how *stable* each device is likely to be
over the next few seconds, makes better placement choices than the standard
rules.

"Distributed" here refers to the *inference* being distributed: per-node
Bayesian modules estimating local viability, feeding a controller-level
layer, rather than one monolithic model.

To test that, this repo is a Python simulator where allocation strategies are
swappable plug-ins, so each one can be run against an identical world and
compared fairly.

## Where the project is right now

- [x] **Stages 1-2** - system model and a working simulation scaffold.
- [x] **Stage 4** - heterogeneous nodes: per-node `cpu_speed`, enforced
      memory, `queue_limit` (overflow tasks are dropped and logged),
      task-type suitability, reusable `node_profiles`. Example:
      `configs/heterogeneous.yaml`.
- [x] **Network realism** - link profiles (LAN / Wi-Fi / 5G) with jitter on
      by default, uplink *and* downlink (results travel home, and the
      deadline counts the return trip), plus `varying_fluid_link` whose link
      quality drifts over time.
- [x] **Workload realism** - per-task property distributions
      (`{dist: uniform|normal|lognormal|exponential|empirical|percentile}`),
      weighted task-type mixes (`task_mix`), and time-varying arrival rates
      (sinusoidal curves, piecewise bursts, measured traces).
- [x] **Control-plane realism** - the controller is not all-knowing:
      `observability: {type: heartbeat, interval, report_delay}` makes it
      decide from stale node reports, and `scheduling_delay` charges each
      decision a fixed orchestration cost.
- [x] **Stage 5** - deterministic baselines: `load_aware`, `latency_first`,
      `weighted_score` (tunable delay/load/compute/energy weights), and
      `reliability_threshold`.
- [ ] **Stage 6** - a best-possible allocation as a reference point (MILP).
- [x] **Stage 7** - trace-informed inputs: real workload rhythms and
      duration/memory percentiles (Azure Functions 2019) and real measured 5G
      bandwidth (UCC Ireland) enter through ordinary plug-ins. Converters live
      in `tools/`; example `configs/trace_driven.yaml` (needs the local
      datasets, see the comments in that file).
- [x] **Stage 8** - instability: a `scenarios:` list scripts node failures
      (tasks lost, heartbeats go silent, recovery at reduced speed) and
      reliability decay. Example: `configs/instability.yaml`.
- [ ] **Stage 9** - the Bayesian allocator and controller hierarchy (the
      actual research contribution).
- [ ] **Stage 10** - comparative experiments and analysis.

Validation: the engine is checked against queueing theory. An M/D/1 workload
reproduces the Pollaczek-Khinchine mean sojourn time (1.5 s) across seeds,
enforced by `tests/test_validation.py`.

## Running it

Needs Python 3.11 or newer (I'm using 3.14).

```bash
python -m venv .venv
source .venv/bin/activate           # macOS / Linux
# .\.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install -r requirements.txt
```

### The UI (easiest way in)

```bash
python -m ui
```

Opens a browser page (default `http://127.0.0.1:8000`) with four tabs:

- **Map** - the topology. Click a device to configure it in a popup, click a
  link to change that pair's connection, drag things around, then Validate
  and Run without leaving the page.
- **Config** - every setting on one page, if you'd rather not click around
  the map. A live YAML panel (the `YAML` button) shows exactly what your
  clicks produce, and you can hand-edit it and watch the forms follow.
- **Replay** - plays a finished run back: tasks appearing, the controller
  being asked and answering, payloads crossing links, queues filling,
  results returning, nodes failing and recovering. Click any device to see
  what is happening inside it at that moment.
- **Compare** - runs several allocators, observability settings, or seeds on
  an identical world and tabulates them side by side, with CSV export.

Every dropdown is read live from the plug-in registries, so a newly
registered allocator (or generator, network model, scenario...) appears in
the UI on its own with its parameters. UI runs write the same log files as
the CLI, under `logs/ui/<run_id>/`, and configs saved from the UI are
ordinary files in `configs/` you can run from a terminal.

### The command line

```bash
python experiments/run_phase1.py configs/phase1.yaml
```

Writes four files into the config's output directory:

- `allocation_log.csv` - one row per task: when it arrived, where it went,
  when it finished, whether it met its deadline.
- `state_log.csv` - per-node queue length, utilisation, reliability and
  failure state over time.
- `config_used.yaml` and `seed.txt` - so the exact run can be reproduced.

Overrides that don't require editing the config:

```bash
python experiments/run_phase1.py configs/phase1.yaml --seed 7 --output-dir logs/my_run --quiet
```

### Tests

```bash
python -m pytest
```

## The configs

| File | What it shows |
|---|---|
| `phase1.yaml` | Minimal baseline run. |
| `heterogeneous.yaml` | The methodology's three-device scenario with the full realism stack switched on. |
| `instability.yaml` | Node failure and reliability decay: tasks get lost. |
| `trace_driven.yaml` | Real Azure workload and real 5G bandwidth (needs local datasets). |
| `load_test.yaml` | Overload behaviour. |

Things you can change without touching Python:

- `seed` - all randomness flows from it. Same config + same seed = same output.
- `sim_duration`, `dt` - how long to simulate, and the tick size. Use
  `dt: 0.01` for real experiments; `1.0` is only for quick smoke tests.
- `nodes` - each is a `source` or `helper` with CPU/memory capacity, and
  optionally `cpu_speed`, `queue_limit`, `accepts_task_types`,
  `gpu_capacity`, `energy_cost_factor`.
- `node_profiles` - named hardware presets nodes adopt via `profile: <name>`;
  fields set on a node override its profile.
- `source.generator` - the arrival pattern and the task properties it
  produces. Any numeric property can be a fixed number or a distribution;
  `rate` can be a number or a pattern; `task_mix` gives weighted task types.
- `controllers` - which controller manages which nodes, which allocator it
  uses, how fresh its view is (`observability`), and `scheduling_delay`.
- `network` - the default link profile plus per-pair overrides.
- `scenarios` - scripted failures and reliability decay.

## Inside the repo

```
src/                    # the simulator
  models/                 # core types: Task, EdgeNode, NodeState, AllocationOutcome
  config/                 # YAML loading, validation, plug-in registries
  generation/             # task generators, distributions, arrival-rate patterns
  controller/             # the controller, observability models, allocators
  network/                # transmission delay models
  simulation/             # clock, per-node processing, scenarios, Environment
  logging_utils/          # writes the CSV log files

ui/                     # browser UI (Flask backend + static frontend)
experiments/            # CLI entry point
tools/                  # dataset converters, benchmark and evidence scripts
configs/                # YAML experiment configs
tests/                  # the pytest suite
logs/                   # simulator outputs (gitignored)
```

## How the plug-in system works

Every swappable part lives in a registry. Adding one is a class plus a
one-line decorator, with no changes to the core:

```python
@allocators.register("my_strategy")
class MyStrategyAllocator(Allocator):
    def allocate(self, context): ...
```

Then in YAML: `allocator: {type: my_strategy}`. The same pattern covers
generators, network models, distributions, arrival-rate patterns,
observability models, and instability scenarios. The UI discovers new
plug-ins automatically by inspecting these registries, so it never needs
updating when one is added.

That is the point of the architecture: the Bayesian allocator of Stage 9
drops into the same slot the baselines use, and gets compared against them on
a bit-for-bit identical world.
