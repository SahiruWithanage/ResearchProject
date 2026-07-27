"""Grid runner for the Compare tab.

Same-world guarantee, exactly like tools/evidence_runs.py: every cell is
the base config with only the variant's fields changed (and optionally
the seed), so outcome differences are attributable to the variant.

A variant is a dict with a ``label`` plus any of:
- ``allocator`` / ``observability`` / ``scheduling_delay`` - applied to
  every controller (``observability: None`` restores the perfect default)
- ``seed`` handling lives in ``run_compare`` via the seeds list
- ``set`` - a deep-merge patch onto the top-level config for anything else
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from src.config import parse_config
from src.simulation.environment import Environment
from ui.metrics import summarize

MAX_CELLS = 60  # grids run sequentially in the request; keep them sane

_CONTROLLER_KEYS = ("allocator", "observability", "scheduling_delay")


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = deepcopy(v)


def _apply_variant(base: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    raw = deepcopy(base)
    if isinstance(variant.get("set"), dict):
        _deep_merge(raw, variant["set"])
    for key in _CONTROLLER_KEYS:
        if key in variant:
            for ctrl in raw.get("controllers", []):
                if variant[key] is None:
                    ctrl.pop(key, None)
                else:
                    ctrl[key] = deepcopy(variant[key])
    return raw


def _validated_seeds(seeds: Any, base_raw: dict[str, Any]) -> list[Any]:
    """Seeds must be an explicit list of integers.

    A bare string is rejected rather than iterated: ``"724"`` would silently
    become the three seeds 7, 2 and 4 and quietly produce the wrong
    experiment.
    """
    if seeds is None or seeds == []:
        return [base_raw.get("seed")]
    if isinstance(seeds, (str, bytes)) or not isinstance(seeds, (list, tuple)):
        raise ValueError(
            f"seeds must be a list of integers, got {type(seeds).__name__}"
        )
    out = []
    for s in seeds:
        if isinstance(s, bool) or not isinstance(s, (int, float)) or int(s) != s:
            raise ValueError(f"seed {s!r} is not an integer")
        out.append(int(s))
    return out


def run_compare(
    base_raw: dict[str, Any],
    variants: list[dict[str, Any]],
    seeds: list[int] | None,
) -> list[dict[str, Any]]:
    if not isinstance(variants, (list, tuple)) or any(
        not isinstance(v, dict) for v in variants
    ):
        raise ValueError("every variant must be an object with a 'label'")
    seed_list = _validated_seeds(seeds, base_raw)
    total = len(variants) * len(seed_list)
    if total == 0:
        raise ValueError("no comparison cells: provide at least one variant")
    if total > MAX_CELLS:
        raise ValueError(
            f"{total} runs requested ({len(variants)} variants x "
            f"{len(seed_list)} seeds); the cap is {MAX_CELLS}"
        )

    cells: list[dict[str, Any]] = []
    for variant in variants:
        varied = _apply_variant(base_raw, variant)
        for seed in seed_list:
            raw = deepcopy(varied)
            if seed is not None:
                raw["seed"] = seed
            t0 = time.perf_counter()
            result = Environment(parse_config(raw)).run()
            wall = time.perf_counter() - t0
            cells.append(
                {
                    "label": str(variant.get("label", "?")),
                    "seed": raw.get("seed"),
                    "summary": summarize(result, wall_seconds=wall),
                }
            )
    return cells
