"""Random allocation: the floor of the comparison ladder."""

from __future__ import annotations

from src.config.factory import allocators
from src.controller.allocators.base import Allocator
from src.controller.context import DecisionContext


@allocators.register("random")
class RandomAllocator(Allocator):
    """Pick uniformly at random among the eligible candidates.

    This is not a strategy anyone would deploy - it is a **reference
    point**. Without it a score has no bottom: 95% might be near-optimal
    or barely better than guessing, and there is no way to tell. Paired
    with an optimal ceiling it turns raw percentages into a statement
    with meaning, e.g. "closes 84% of the gap between naive and optimal".

    It still respects eligibility, because the Controller filters
    candidates before any allocator runs. The floor is about the quality
    of the *decision*, not about being allowed to break constraints - a
    random allocator that violated queue limits would be measuring
    something else entirely.

    Randomness comes from the Controller's **dedicated** stream, spawned
    separately from the task generators and the network, so however many
    draws this makes it cannot shift arrivals or jitter. That keeps the
    world identical to the one every other allocator faces, which is the
    whole basis of the comparison.
    """

    def allocate(self, context: DecisionContext) -> str:
        if not context.candidates:
            raise ValueError("allocate() requires at least one candidate")
        if context.rng is None:
            raise ValueError(
                "the random allocator needs a random stream; the Controller "
                "was built without one. Going silently deterministic would "
                "make this a disguised 'always pick the first node' rule."
            )
        index = int(context.rng.integers(len(context.candidates)))
        return context.candidates[index].node_id
