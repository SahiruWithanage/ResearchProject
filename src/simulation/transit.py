"""In-transit tasks waiting for uplink delivery."""

from __future__ import annotations

from dataclasses import dataclass

from src.models import Task


@dataclass(slots=True)
class TransitEntry:
    task: Task
    target_node_id: str
    arrive_at: float


class TransitQueue:
    """Tasks that have been allocated remotely but not yet at the executor."""

    def __init__(self) -> None:
        self._entries: list[TransitEntry] = []

    def schedule(self, task: Task, target_node_id: str, arrive_at: float) -> None:
        self._entries.append(
            TransitEntry(task=task, target_node_id=target_node_id, arrive_at=arrive_at)
        )

    def deliver_due(self, t_end: float) -> list[TransitEntry]:
        """Return entries with ``arrive_at <= t_end``, removing them from the queue."""
        due = [e for e in self._entries if e.arrive_at <= t_end + 1e-12]
        self._entries = [e for e in self._entries if e.arrive_at > t_end + 1e-12]
        due.sort(key=lambda e: (e.arrive_at, e.task.task_id))
        return due

    def __len__(self) -> int:
        return len(self._entries)
