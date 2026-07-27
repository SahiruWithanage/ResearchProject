"""Pluggable value distributions for per-task properties.

Anywhere a generator accepts a numeric task property (``cpu_demand``,
``data_size``, ``result_size``, ...) the YAML may give either a plain
number (constant, consumes no randomness) or a distribution spec:

.. code-block:: yaml

    cpu_demand: 2.0                                # constant
    data_size: {dist: uniform, low: 1e5, high: 9e5}
    result_size: {dist: lognormal, mean: 11.0, sigma: 0.5}

New distributions register themselves like every other plug-in:
``@distributions.register("name")`` with keyword-only constructor args
matching the YAML keys.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

import numpy as np

from src.config.factory import distributions

# Shadow-proof aliases (NormalDistribution's kwargs shadow the builtins).
builtins_min = min
builtins_max = max


class Distribution(ABC):
    """Contract: ``sample(rng)`` returns one float draw.

    ``is_constant`` lets callers avoid requiring an rng when nothing
    actually needs randomness (keeps old constant-only configs
    byte-identical in their random streams).
    """

    is_constant: bool = False

    @abstractmethod
    def sample(self, rng: np.random.Generator | None) -> float: ...


@distributions.register("constant")
class ConstantDistribution(Distribution):
    is_constant = True

    def __init__(self, *, value: float) -> None:
        self.value = float(value)

    def sample(self, rng: np.random.Generator | None) -> float:
        return self.value


@distributions.register("uniform")
class UniformDistribution(Distribution):
    def __init__(self, *, low: float, high: float) -> None:
        if high < low:
            raise ValueError(f"uniform needs low <= high, got {low} > {high}")
        self.low = float(low)
        self.high = float(high)

    def sample(self, rng: np.random.Generator | None) -> float:
        return float(rng.uniform(self.low, self.high))


@distributions.register("normal")
class NormalDistribution(Distribution):
    """Gaussian, optionally clipped to [min, max] after sampling."""

    def __init__(
        self,
        *,
        mean: float,
        std: float,
        min: float | None = None,
        max: float | None = None,
    ) -> None:
        if std < 0:
            raise ValueError(f"normal std must be >= 0, got {std}")
        self.mean = float(mean)
        self.std = float(std)
        self.min = None if min is None else float(min)
        self.max = None if max is None else float(max)

    def sample(self, rng: np.random.Generator | None) -> float:
        value = float(rng.normal(self.mean, self.std))
        if self.min is not None:
            value = builtins_max(value, self.min)
        if self.max is not None:
            value = builtins_min(value, self.max)
        return value


@distributions.register("lognormal")
class LogNormalDistribution(Distribution):
    """numpy semantics: ``mean``/``sigma`` describe the underlying normal.

    Handy fact: the *median* of the result is ``e ** mean`` - e.g.
    ``mean = 11.0`` gives a median around 60 000.
    """

    def __init__(self, *, mean: float, sigma: float) -> None:
        if sigma < 0:
            raise ValueError(f"lognormal sigma must be >= 0, got {sigma}")
        self.mean = float(mean)
        self.sigma = float(sigma)

    def sample(self, rng: np.random.Generator | None) -> float:
        return float(rng.lognormal(self.mean, self.sigma))


@distributions.register("exponential")
class ExponentialDistribution(Distribution):
    def __init__(self, *, mean: float) -> None:
        if mean <= 0:
            raise ValueError(f"exponential mean must be > 0, got {mean}")
        self.mean = float(mean)

    def sample(self, rng: np.random.Generator | None) -> float:
        return float(rng.exponential(self.mean))


@distributions.register("empirical")
class EmpiricalDistribution(Distribution):
    """Sample uniformly from observed values (trace-informed inputs).

    Give either ``values`` inline or ``file`` + ``column`` pointing at a
    CSV of real measurements. ``scale`` converts units on load (e.g.
    milliseconds -> work units).
    """

    def __init__(
        self,
        *,
        values: list[float] | None = None,
        file: str | None = None,
        column: str | None = None,
        scale: float = 1.0,
    ) -> None:
        if (values is None) == (file is None):
            raise ValueError("empirical needs exactly one of 'values' or 'file'")
        if file is not None:
            if column is None:
                raise ValueError("empirical with 'file' also needs 'column'")
            values = _load_csv_column(file, column)
        if not values:
            raise ValueError("empirical needs at least one value")
        self._values = np.asarray([float(v) * scale for v in values])

    def sample(self, rng: np.random.Generator | None) -> float:
        return float(self._values[rng.integers(len(self._values))])


@distributions.register("percentile")
class PercentileDistribution(Distribution):
    """Sample from a distribution described by its percentiles.

    ``points`` is a list of ``[percentile, value]`` pairs (percentile in
    [0, 100], ascending). Sampling draws u ~ U(0, 100) and inverts the
    piecewise-linear CDF. This matches how the Azure Functions 2019
    dataset publishes per-function durations and memory. ``scale``
    converts units (e.g. 0.001 for milliseconds -> seconds).
    """

    def __init__(self, *, points: list[list[float]], scale: float = 1.0) -> None:
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("percentile needs at least two [pct, value] points")
        pct = [float(p[0]) for p in points]
        val = [float(p[1]) * scale for p in points]
        if pct != sorted(pct) or pct[0] < 0 or pct[-1] > 100:
            raise ValueError(
                "percentile points must be ascending with pct in [0, 100]"
            )
        if val != sorted(val):
            raise ValueError("percentile values must be non-decreasing")
        self._pct = np.asarray(pct)
        self._val = np.asarray(val)

    def sample(self, rng: np.random.Generator | None) -> float:
        u = float(rng.uniform(self._pct[0], self._pct[-1]))
        return float(np.interp(u, self._pct, self._val))


def _load_csv_column(path: str, column: str) -> list[float]:
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise ValueError(
                f"column {column!r} not found in {path!r} "
                f"(has: {reader.fieldnames})"
            )
        return [float(row[column]) for row in reader if row[column] != ""]


def build_distribution(spec: Any, where: str = "value") -> Distribution:
    """Turn a YAML value into a Distribution.

    Numbers become :class:`ConstantDistribution`; mappings must carry a
    ``dist`` key naming a registered distribution, the rest are kwargs.
    """
    if isinstance(spec, bool):
        raise ValueError(f"{where}: booleans are not valid values")
    if isinstance(spec, (int, float)):
        return ConstantDistribution(value=float(spec))
    if isinstance(spec, Mapping):
        params = dict(spec)
        name = params.pop("dist", None)
        if not isinstance(name, str):
            raise ValueError(
                f"{where}: distribution spec needs a 'dist' name, got {spec!r}"
            )
        cls = distributions.get(name)
        try:
            return cls(**params)
        except TypeError as e:
            raise ValueError(f"{where}: bad params for dist {name!r}: {e}") from e
    raise ValueError(
        f"{where}: expected a number or a distribution mapping, got "
        f"{type(spec).__name__}: {spec!r}"
    )
