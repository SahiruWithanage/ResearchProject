"""Convert a finished run into the replay timeline the browser animates.

Zero new instrumentation: everything the animation needs already exists -
AllocationOutcome carries each task's full lifecycle timestamps (decision,
uplink transfer, compute, completion, return leg, lost flag) and the
NodeState snapshots carry queue depth / reliability / failure state over
time at the config's `log_state_every` cadence.
"""

from __future__ import annotations

from typing import Any

from src.simulation.environment import EnvironmentResult


def build_timeline(
    result: EnvironmentResult, raw_config: dict[str, Any]
) -> dict[str, Any]:
    raw_nodes = raw_config.get("nodes") or []
    nodes = [
        {"id": n.get("id"), "type": n.get("type")}
        for n in raw_nodes
        if isinstance(n, dict)
    ]
    controllers = []
    for c in raw_config.get("controllers") or []:
        if not isinstance(c, dict):
            continue
        obs = c.get("observability") or {}
        controllers.append(
            {
                "id": c.get("id"),
                "manages": c.get("manages") or [],
                # lets the replay animate heartbeat reports and dispatch
                "observability": {
                    "type": obs.get("type", "perfect"),
                    "interval": obs.get("interval"),
                    "report_delay": obs.get("report_delay", 0.0),
                },
                "scheduling_delay": c.get("scheduling_delay", 0.0),
            }
        )
    network = raw_config.get("network") or {}
    links = [
        {"from": l.get("from"), "to": l.get("to"), "profile": l.get("profile")}
        for l in (network.get("links") or [])
        if isinstance(l, dict)
    ]
    # The replay map draws the same picture as the build map, so it needs the
    # default profile every un-overridden pair communicates over.
    network_info = {
        "type": network.get("type", "instant"),
        "default_profile": network.get("default_profile", "wifi"),
        "configured": bool(network),
    }

    tasks = []
    for o in result.outcomes:
        # task ids are "<source_node_id>_<counter>" (see the generators)
        source = o.task_id.rsplit("_", 1)[0]
        tasks.append(
            {
                "id": o.task_id,
                "source": source,
                "node": o.selected_node,
                "arrival": o.arrival_time,
                "decision": o.decision_time,
                "t_start": o.transfer_start,
                "t_end": o.transfer_end,
                "c_start": o.compute_start,
                "done": o.actual_completion_time,
                "ret": o.return_end,
                "met": o.deadline_met,
                "lost": o.task_lost,
            }
        )
    tasks.sort(key=lambda t: t["decision"])

    states: dict[str, list[list[Any]]] = {}
    for s in result.snapshots:
        states.setdefault(s.node_id, []).append(
            [
                s.time_step,
                s.queue_length,
                s.active_tasks,
                s.reliability_score,
                s.failure_state,
            ]
        )
    for rows in states.values():
        rows.sort(key=lambda r: r[0])

    return {
        "duration": result.final_time,
        "nodes": nodes,
        "controllers": controllers,
        "links": links,
        "network": network_info,
        "tasks": tasks,
        "states": states,
    }
