"""Plain-language help for every option the UI shows.

Written for someone who has never read the code: what the field means, what
units it is in, and what happens if you change it. Class docstrings are
written for developers and are too terse (or too jargon-heavy) to serve as
tooltips, so the text lives here instead.

Lookup order for a parameter, most specific first:
    (registry, plugin, param) -> (registry, "*", param) -> param
"""

from __future__ import annotations

from typing import Any

# What each plug-in is, in one sentence.
PLUGIN_HELP: dict[str, dict[str, str]] = {
    "allocators": {
        "local_first_helper_offload": (
            "Keep every task on the node that created it until that node's "
            "queue passes a limit, then send the overflow to a helper. The "
            "simple rule the project started with."
        ),
        "load_aware": (
            "Send each task to whichever node currently has the least work "
            "waiting. Balances the load, but ignores how far away a node is."
        ),
        "latency_first": (
            "Send each task to whichever node it can reach fastest, ignoring "
            "how busy that node already is. Avoids the network, pays in queueing."
        ),
        "weighted_score": (
            "Score every node with a formula you control (network delay, "
            "queue wait, compute speed, energy) and pick the lowest. With the "
            "default weights this means the earliest expected finish time."
        ),
        "reliability_threshold": (
            "Ignore nodes whose reliability score has dropped below your "
            "threshold, then balance load among the rest. Falls back to using "
            "everything if no node looks trustworthy."
        ),
    },
    "generators": {
        "poisson": (
            "Tasks appear at random moments, at an average rate you set. This "
            "is the standard model for many independent devices sending work "
            "without coordinating."
        ),
        "fixed_interval": (
            "Tasks appear like clockwork, one every N seconds. Predictable, "
            "which makes it useful for testing and debugging."
        ),
    },
    "network_models": {
        "instant": (
            "No network at all: tasks teleport between nodes with zero delay. "
            "Only for quick tests where you don't care about transfer time."
        ),
        "fluid_link": (
            "The realistic default. Transfer time = a fixed base latency, plus "
            "the payload size divided by the bandwidth, plus a bit of random "
            "jitter."
        ),
        "varying_fluid_link": (
            "Like fluid_link, but link quality drifts up and down over time, "
            "as real wireless links do when conditions change."
        ),
        "trace_fluid_link": (
            "Like fluid_link, but replays real measured bandwidth from a CSV "
            "file, so links behave exactly as they did in a real recording."
        ),
    },
    "observability_models": {
        "perfect": (
            "The controller always sees the true, current state of every node. "
            "Unrealistic, but useful as a best-case reference."
        ),
        "heartbeat": (
            "Nodes report their state every few seconds and the report takes "
            "time to arrive, so the controller decides using slightly old "
            "information. This is how real systems work."
        ),
    },
    "distributions": {
        "constant": "Always exactly the same value.",
        "uniform": "A random value anywhere between low and high, all equally likely.",
        "normal": (
            "A random value clustered around a mean, in the classic bell-curve "
            "shape. Most values land near the mean, extremes are rare."
        ),
        "lognormal": (
            "Random values that are mostly small but occasionally very large. "
            "Fits real task sizes and durations well."
        ),
        "exponential": (
            "Random values where small is common and large is increasingly "
            "rare. The classic model for waiting times."
        ),
        "empirical": (
            "Pick randomly from a list of real observed values (typed in, or "
            "read from a column of a CSV file)."
        ),
        "percentile": (
            "Rebuild a distribution from published percentiles, e.g. '50% of "
            "tasks finish under 20ms, 99% under 400ms'. This is how the Azure "
            "dataset reports its numbers."
        ),
    },
    "rate_patterns": {
        "constant": "The arrival rate never changes.",
        "sinusoidal": (
            "The arrival rate rises and falls smoothly in a repeating cycle, "
            "like day/night traffic."
        ),
        "piecewise": (
            "You define the rate in blocks of time, e.g. quiet, then a burst, "
            "then quiet again."
        ),
        "trace": (
            "Replay a real measured arrival rate over time from a CSV file."
        ),
    },
    "scenarios": {
        "node_failure": (
            "Crash a node at a chosen time. It loses everything it was "
            "holding, stops working, and stops reporting, then optionally "
            "recovers later at reduced speed."
        ),
        "reliability_decay": (
            "Gradually lower a node's reliability score over a period, "
            "simulating a device that is becoming untrustworthy without "
            "actually failing."
        ),
    },
}

# Structural config fields (not plug-in parameters).
FIELD_HELP: dict[str, str] = {
    "seed": (
        "The number that drives all randomness. The same seed always gives "
        "exactly the same run, which is what makes comparisons fair and "
        "results reproducible."
    ),
    "sim_duration": "How many seconds of simulated time to run for.",
    "dt": (
        "The simulator's tick size in seconds: how often it re-checks the "
        "world. Use 0.01 for real experiments. Larger values run faster but "
        "distort short network delays."
    ),
    "output_dir": "Folder the result files are written to.",
    "log_state_every": (
        "How often (in simulated seconds) to record a snapshot of every node. "
        "Smaller values give a smoother replay and bigger log files."
    ),
    "id": "The device's name. Must be unique, and is used in all the logs.",
    "type": (
        "'source' generates its own tasks and can also run them. 'helper' only "
        "runs tasks sent to it by others."
    ),
    "profile": (
        "Use a named hardware preset instead of setting each spec by hand. "
        "Anything you set directly on the node overrides the preset."
    ),
    "tier": "A label for where this device sits, e.g. edge or cloud.",
    "location": (
        "Where this device physically sits, as [x, y] in kilometres. Giving "
        "nodes locations lets the network work out real distances between "
        "them, which adds signal travel time and (if path loss is on) weakens "
        "far-away links. Leave it out and the topology has no geometry."
    ),
    "cpu_capacity": (
        "How many tasks this node can work on at the same time (its number of "
        "workers). 4 means four tasks run in parallel."
    ),
    "cpu_speed": (
        "How fast each worker gets through work, in work-units per second. "
        "0.5 is a half-speed device: the same task takes twice as long."
    ),
    "memory_capacity": (
        "Total memory available. A queued task only starts once there is "
        "enough free memory for it."
    ),
    "queue_limit": (
        "The most tasks this node will hold. Full nodes are skipped by the "
        "allocator. If nowhere has room, the task is dropped and logged as "
        "lost. Leave empty for no limit."
    ),
    "accepts_task_types": (
        "Only accept these kinds of task, e.g. a sensor that can handle "
        "telemetry but not video analytics. Leave empty to accept everything."
    ),
    "gpu_capacity": "How much GPU this node has, for tasks that need one.",
    "energy_cost_factor": (
        "How expensive this node is to run, relative to others. Only matters "
        "if your allocator weighs energy."
    ),
    "manages": "Which nodes this controller decides for.",
    "parent": "A controller above this one in a hierarchy. Usually empty.",
    "host": (
        "Which device this controller physically runs on. Giving it a place "
        "in the topology means its status reports travel real network links "
        "instead of a fixed delay. Leave empty for a controller with no "
        "location."
    ),
    "scheduling_delay": (
        "Seconds of overhead between the controller deciding and the task "
        "starting to move: its own processing plus sending the instruction. "
        "Kept identical for every allocator so comparisons stay fair."
    ),
    "default_profile": (
        "The connection every pair of nodes uses unless you give that pair its "
        "own link. lan is fast and steady, wifi and 5g are slower with more "
        "variation."
    ),
    "max_range_km": (
        "How far this kind of connection physically carries, in kilometres. "
        "Two devices further apart than this cannot reach each other at all, "
        "so neither is a candidate for the other's work. Only applies once "
        "devices have positions."
    ),
    "full_rate_km": (
        "How close two devices must be to get this connection's full speed. "
        "Past this distance the signal weakens and bandwidth drops off. "
        "Wi-Fi starts fading within metres; a 5G cell holds its rate for "
        "hundreds."
    ),
    "profiles": (
        "Advanced: override the built-in link presets, e.g. change what 'wifi' "
        "means. JSON format."
    ),
}

# Parameter help. Keys: (registry, plugin, param), (registry, "*", param),
# or a bare param name as the last-resort fallback.
PARAM_HELP: dict[Any, str] = {
    # --- allocators ---
    ("allocators", "local_first_helper_offload", "max_local_queue"): (
        "Once the source node has this many tasks waiting, send new ones to a "
        "helper instead."
    ),
    ("allocators", "weighted_score", "w_delay"): (
        "How much to care about network time (sending the task there and the "
        "result back). Higher means 'prefer nearby nodes'."
    ),
    ("allocators", "weighted_score", "w_load"): (
        "How much to care about the queue already waiting on a node. Higher "
        "means 'prefer idle nodes'."
    ),
    ("allocators", "weighted_score", "w_compute"): (
        "How much to care about how fast the node computes. Higher means "
        "'prefer powerful nodes'."
    ),
    ("allocators", "weighted_score", "w_energy"): (
        "How much to care about energy cost. Higher means 'avoid expensive "
        "nodes'. Zero (the default) ignores energy entirely."
    ),
    ("allocators", "reliability_threshold", "min_reliability"): (
        "Nodes scoring below this (0 to 1) are avoided. 0.5 means 'skip "
        "anything less than half trustworthy'."
    ),
    # --- observability ---
    ("observability_models", "heartbeat", "interval"): (
        "How often each node reports its state, in seconds. Longer intervals "
        "mean the controller works from staler information."
    ),
    ("observability_models", "heartbeat", "report_bytes"): (
        "How big a status report is, in bytes. Only matters when the "
        "controller is hosted on a device: the report then crosses real "
        "links, so a slow or busy link genuinely delays what the controller "
        "knows."
    ),
    ("observability_models", "heartbeat", "report_delay"): (
        "How long a report takes to reach the controller, in seconds. So the "
        "controller's view is always at least this old."
    ),
    # --- generators ---
    ("generators", "*", "rate"): (
        "Average number of tasks generated per second. Can be a fixed number "
        "or a pattern that changes over time."
    ),
    ("generators", "fixed_interval", "interval"): (
        "Seconds between tasks. 2.0 means one task every two seconds."
    ),
    ("generators", "fixed_interval", "offset"): (
        "Wait this many seconds before generating the first task."
    ),
    ("generators", "*", "task_type"): (
        "A label for this kind of work, e.g. telemetry or analytics. Nodes can "
        "be set to accept only certain types."
    ),
    ("generators", "*", "cpu_demand"): (
        "How much work the task is, in work-units. A node with cpu_speed 1.0 "
        "gets through one unit per second."
    ),
    ("generators", "*", "memory_demand"): (
        "How much memory the task needs while running."
    ),
    ("generators", "*", "gpu_demand"): "How much GPU the task needs, if any.",
    ("generators", "*", "data_size"): (
        "Size of the task's input in BYTES, sent across the network if the "
        "task runs on another node. 500000 is 500 KB."
    ),
    ("generators", "*", "result_size"): (
        "Size of the answer in BYTES, sent back to the source when the task "
        "finishes elsewhere. Set 0 if nothing comes back."
    ),
    ("generators", "*", "deadline_offset"): (
        "Seconds after the task appears by which it must be finished and "
        "returned. This is what 'met its deadline' is measured against."
    ),
    ("generators", "*", "priority"): "Task importance. Not used yet.",
    ("generators", "*", "task_mix"): (
        "Define several kinds of task with different weights, instead of one "
        "kind. Weights are relative: 0.9 and 0.1 means roughly nine of the "
        "first for every one of the second."
    ),
    ("generators", "*", "weight"): (
        "How common this kind of task is, relative to the others."
    ),
    # --- network ---
    ("network_models", "varying_fluid_link", "variation_period_s"): (
        "How many seconds one cycle of link-quality drift lasts."
    ),
    ("network_models", "varying_fluid_link", "bandwidth_variation"): (
        "How much bandwidth swings, as a fraction. 0.5 means it varies by up "
        "to 50% above and below normal."
    ),
    ("network_models", "varying_fluid_link", "latency_variation"): (
        "How much the base latency swings, as a fraction."
    ),
    ("network_models", "trace_fluid_link", "traces"): (
        "Which recorded bandwidth file to replay on which link."
    ),
    ("network_models", "*", "propagation_speed_kms"): (
        "How fast a signal travels, in km per second. The default 200,000 "
        "is roughly light speed in fibre, which works out to about 5 "
        "microseconds per kilometre."
    ),
    ("network_models", "*", "path_loss_exponent"): (
        "How sharply a wireless link weakens with distance. 0 (the default) "
        "turns the effect off. Around 2 is free space, 3 to 4 is typical "
        "indoors or in a built-up area. Only applies to nodes that have a "
        "location set."
    ),
    ("network_models", "*", "path_loss_reference_km"): (
        "Distance at which a link still runs at full speed. Beyond this, "
        "bandwidth starts dropping off."
    ),
    ("network_models", "*", "min_bandwidth_fraction"): (
        "Never let distance cut bandwidth below this fraction of normal, so "
        "a far-away link degrades rather than silently dying. Use "
        "'no connection' on a link if you want to cut it off entirely."
    ),
    ("network_models", "*", "file"): "Path to the CSV file, relative to the project folder.",
    ("network_models", "*", "loop"): (
        "Start the recording again from the beginning when it runs out."
    ),
    ("network_models", "*", "bandwidth_scale"): (
        "Multiply every recorded bandwidth by this, e.g. 0.5 to simulate a "
        "connection half as good."
    ),
    ("network_models", "*", "time_scale"): (
        "Stretch or squash the recording in time. 2.0 makes it play at half "
        "speed."
    ),
    ("network_models", "*", "min_bandwidth_bps"): (
        "Never let bandwidth drop below this, so a dead spot in the recording "
        "doesn't stall a transfer forever."
    ),
    # --- distributions ---
    ("distributions", "constant", "value"): "The fixed value to always use.",
    ("distributions", "uniform", "low"): "Smallest possible value.",
    ("distributions", "uniform", "high"): "Largest possible value.",
    ("distributions", "normal", "mean"): "The centre of the bell curve.",
    ("distributions", "normal", "std"): (
        "How spread out the values are. Larger means more variation."
    ),
    ("distributions", "normal", "min"): "Never produce a value below this.",
    ("distributions", "normal", "max"): "Never produce a value above this.",
    ("distributions", "lognormal", "mean"): (
        "The mean of the underlying logarithm, not of the values themselves."
    ),
    ("distributions", "lognormal", "sigma"): (
        "Spread of the underlying logarithm. Bigger means a longer tail of "
        "large values."
    ),
    ("distributions", "exponential", "mean"): "The average value produced.",
    ("distributions", "empirical", "values"): "A list of observed values to sample from.",
    ("distributions", "empirical", "file"): "CSV file to read observed values from.",
    ("distributions", "empirical", "column"): "Which column of that CSV to read.",
    ("distributions", "*", "scale"): (
        "Multiply every value by this, to convert units. 0.001 turns "
        "milliseconds into seconds."
    ),
    ("distributions", "percentile", "points"): (
        "Pairs of [percentile, value], e.g. [[50, 20], [99, 400]] for 'half "
        "are under 20, 99% are under 400'."
    ),
    # --- rate patterns ---
    ("rate_patterns", "constant", "value"): "Tasks per second, unchanging.",
    ("rate_patterns", "sinusoidal", "base"): (
        "The average tasks per second, around which the rate rises and falls."
    ),
    ("rate_patterns", "sinusoidal", "amplitude"): (
        "How far the rate swings above and below the base. Keep it below the "
        "base or the rate would go negative."
    ),
    ("rate_patterns", "sinusoidal", "period"): (
        "Seconds for one full cycle, e.g. 120 for a two-minute wave."
    ),
    ("rate_patterns", "sinusoidal", "phase"): "Shifts where in the cycle the run starts.",
    ("rate_patterns", "piecewise", "segments"): (
        "Blocks of time with their own rates, each needing t_start and rate."
    ),
    ("rate_patterns", "trace", "rate_scale"): "Multiply every recorded rate by this.",
    ("rate_patterns", "trace", "file"): "CSV file with columns t and rate.",
    ("rate_patterns", "trace", "time_scale"): (
        "Stretch or squash the recording in time. 2.0 makes a one-hour "
        "recording play out over two hours."
    ),
    ("rate_patterns", "trace", "loop"): (
        "Start the recording again from the beginning when it runs out, "
        "instead of dropping to no tasks at all."
    ),
    # --- scenarios ---
    ("scenarios", "*", "node"): "Which device this happens to.",
    ("scenarios", "node_failure", "fail_at"): (
        "The second at which the node crashes. Everything it holds is lost."
    ),
    ("scenarios", "node_failure", "recover_at"): (
        "The second at which it starts coming back. Leave empty to stay dead."
    ),
    ("scenarios", "node_failure", "recovery_duration"): (
        "Seconds it spends running at reduced speed before returning to normal."
    ),
    ("scenarios", "node_failure", "recovery_speed_factor"): (
        "How fast it runs while recovering. 0.5 is half its usual speed."
    ),
    ("scenarios", "reliability_decay", "start"): "Second at which the decline begins.",
    ("scenarios", "reliability_decay", "end"): "Second at which it reaches its final value.",
    ("scenarios", "reliability_decay", "from_value"): "Reliability score before the decline (1.0 is perfect).",
    ("scenarios", "reliability_decay", "to_value"): "Reliability score it ends up at.",
    # --- bare fallbacks ---
    "from": "The device the connection starts at.",
    "to": "The device the connection goes to.",
}


def plugin_help(registry: str, name: str) -> str:
    return PLUGIN_HELP.get(registry, {}).get(name, "")


def param_help(registry: str | None, plugin: str | None, param: str) -> str:
    for key in ((registry, plugin, param), (registry, "*", param), param):
        text = PARAM_HELP.get(key)
        if text:
            return text
    return FIELD_HELP.get(param, "")


def field_help(name: str) -> str:
    return FIELD_HELP.get(name, "")
