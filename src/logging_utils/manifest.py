"""Run manifest: everything needed to prove a result came from a given
code state, on a given machine, and to verify a reproduction.

The CSVs say *what happened*. The manifest says *what produced it*: the
exact commit, the interpreter and library versions, the platform, the
seed, and checksums of the outputs. Without it a number in the thesis is
untraceable - "95.9%" from which code, which numpy, which machine?

Reproduction check: rerun the config and compare `outputs`. Identical
checksums mean a byte-exact reproduction. Differing ones with an
identical `code` and `environment` block point at a genuine determinism
bug rather than an environment difference.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1

# Recorded because they can influence numerical results or RNG streams.
_TRACKED_PACKAGES = ("numpy", "pyyaml", "flask", "pytest")


def _git(args: list[str], repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", errors="replace").strip() or None


def _code_state(repo: Path) -> dict[str, Any]:
    """Which commit produced this, and were there uncommitted edits?

    `dirty` matters: a result produced from a modified working tree cannot
    be reproduced from the commit alone.
    """
    status = _git(["status", "--porcelain"], repo)
    return {
        "git_commit": _git(["rev-parse", "HEAD"], repo),
        "git_branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], repo),
        "git_dirty": bool(status) if status is not None else None,
    }


def _environment() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for name in _TRACKED_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "packages": packages,
    }


def sha256_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *,
    repo_root: Path,
    seed: int,
    sim_duration: float,
    dt: float,
    allocators: list[str],
    output_files: dict[str, Path],
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        # Volatile by nature: excluded from any reproduction comparison.
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code": _code_state(repo_root),
        "environment": _environment(),
        "run": {
            "seed": seed,
            "sim_duration": sim_duration,
            "dt": dt,
            "allocators": allocators,
        },
        # Compare these to verify a reproduction.
        "outputs": {
            name: sha256_of(path) for name, path in sorted(output_files.items())
        },
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
