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
