from agentlab.corpus import generate_session


def test_deterministic_by_seed():
    a, b = generate_session(seed=7), generate_session(seed=7)
    assert a.transcript == b.transcript and a.facts == b.facts


def test_different_seeds_differ():
    assert generate_session(seed=1).transcript != generate_session(seed=2).transcript


def test_facts_present_in_transcript():
    s = generate_session(seed=3)
    joined = "\n".join(s.transcript)
    assert len(s.facts) == 12
    for f in s.facts:
        assert f.value in joined


def test_probe_answerable():
    s = generate_session(seed=3)
    for f in s.facts:
        assert f.key in f.probe_question


def test_facts_planted_strictly_before_boundary():
    # Every planted fact must land before the compaction boundary, or it
    # appears verbatim in both the truncate and structured-summary arms and
    # dilutes the measured recall delta between them.
    from agentlab.compaction_task import BOUNDARY_FRACTION

    for seed in range(15):
        s = generate_session(seed=seed)
        boundary = int(BOUNDARY_FRACTION * len(s.transcript))
        for fact in s.facts:
            positions = [i for i, turn in enumerate(s.transcript) if fact.value in turn]
            assert positions, f"fact {fact.key} not found in transcript (seed={seed})"
            assert all(pos < boundary for pos in positions), (
                f"fact {fact.key} planted at turn(s) {positions} on or after "
                f"boundary {boundary} (seed={seed})"
            )
