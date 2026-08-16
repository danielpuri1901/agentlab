import random

from faker import Faker
from pydantic import BaseModel

DEFAULT_PLANT_FRACTION = 0.6
"""Default fraction of a session's filler turns, counted from the start, in
which facts may be planted. Must equal compaction_task.BOUNDARY_FRACTION so
every planted fact lands strictly before the compaction boundary - otherwise
post-boundary facts appear verbatim in both the truncate and structured-summary
arms and dilute the measured delta between them. compaction_task imports this
constant as the single source of truth for both values."""


class Fact(BaseModel, frozen=True):
    key: str
    value: str
    probe_question: str


class Session(BaseModel):
    transcript: list[str]
    facts: list[Fact]


def generate_session(
    seed: int,
    n_facts: int = 12,
    filler_turns: int = 40,
    plant_fraction: float = DEFAULT_PLANT_FRACTION,
) -> Session:
    """Build a synthetic session transcript with `n_facts` planted facts.

    Facts are planted only in the first `plant_fraction` of `filler_turns`
    turns, so every fact lands strictly before the compaction boundary (see
    DEFAULT_PLANT_FRACTION).
    """
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)
    facts = []
    for i in range(n_facts):
        key = f"{fake.word()}-{i}-{rng.randint(100, 999)}"
        value = f"CODE-{rng.randint(10_000, 99_999)}"
        facts.append(
            Fact(
                key=key,
                value=value,
                probe_question=f"What was the exact code recorded for {key}?",
            )
        )
    roles = ["user", "assistant"]
    turns = [f"{roles[i % 2]}: {fake.sentence(nb_words=12)}" for i in range(filler_turns)]
    plantable_turns = int(plant_fraction * filler_turns)
    positions = sorted(rng.sample(range(plantable_turns), n_facts))
    for pos, fact in zip(positions, facts):
        turns[pos] = f"assistant: note for the record, {fact.key} resolved with {fact.value}."
    return Session(transcript=turns, facts=facts)
