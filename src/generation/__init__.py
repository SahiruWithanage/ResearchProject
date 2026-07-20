"""Task generators: pluggable arrival models behind a common ABC."""

from . import distributions as _distributions  # noqa: F401 — register plug-ins
from . import rate_patterns as _rate_patterns  # noqa: F401 — register plug-ins
from .base import TaskGenerator
from .fixed_interval import FixedIntervalGenerator
from .poisson import PoissonGenerator
from .task_builder import TaskBuilder

__all__ = [
    "TaskGenerator",
    "PoissonGenerator",
    "FixedIntervalGenerator",
    "TaskBuilder",
]
