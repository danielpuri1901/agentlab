import random

from faker import Faker
from pydantic import BaseModel


class Fact(BaseModel, frozen=True):
    key: str
    value: str
    probe_question: str


class Session(BaseModel):
    transcript: list[str]
    facts: list[Fact]


def generate_session(seed: int, n_facts: int = 12, filler_turns: int = 40) -> Session:
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
    positions = sorted(rng.sample(range(filler_turns), n_facts))
    for pos, fact in zip(positions, facts):
        turns[pos] = f"assistant: note for the record, {fact.key} resolved with {fact.value}."
    return Session(transcript=turns, facts=facts)
