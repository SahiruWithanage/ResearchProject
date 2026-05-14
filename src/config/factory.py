"""Plugin registries for generators and allocators.

This is the plug-and-play seam: components decorate themselves with
``@generators.register("name")`` or ``@allocators.register("name")`` at
import time, and configs select them by name. Adding a new generator
or allocator is a one-line registration plus an import.
"""

from __future__ import annotations
from typing import Callable, TypeVar

T = TypeVar("T")


class Registry:
    """Name -> class lookup. Populated by @register decorators at import time."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, type] = {}

    def register(self, name: str) -> Callable[[type[T]], type[T]]:
        def decorator(cls: type[T]) -> type[T]:
            if name in self._items:
                existing = self._items[name].__name__
                raise ValueError(
                    f"{self._kind} {name!r} is already registered to {existing!r}; "
                    f"cannot also register {cls.__name__!r}"
                )
            self._items[name] = cls
            return cls

        return decorator

    def get(self, name: str) -> type:
        if name not in self._items:
            known = sorted(self._items) or ["<none registered>"]
            raise KeyError(f"unknown {self._kind} {name!r}. registered: {known}")
        return self._items[name]

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items

    def __len__(self) -> int:
        return len(self._items)


generators = Registry("generator")
allocators = Registry("allocator")
