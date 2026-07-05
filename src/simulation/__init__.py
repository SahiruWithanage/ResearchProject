"""Core simulator: clock, environment, and per-node processing."""

from .clock import Clock
from .processing import NodeRuntime

__all__ = ["Clock", "Environment", "EnvironmentResult", "NodeRuntime"]


def __getattr__(name: str):
    if name in ("Environment", "EnvironmentResult"):
        from .environment import Environment, EnvironmentResult

        return Environment if name == "Environment" else EnvironmentResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
