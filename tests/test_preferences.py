from agentlab.preferences import candidate_topics, normalise_topic


def test_topic_normalisation_uses_fuzzy_aliases_without_embeddings():
    assert normalise_topic("agent eval") == "agent-evals"
    assert normalise_topic("model graders") == "agent-evals"
    assert normalise_topic("recursve self improvement") == "self-improvement"


def test_candidate_topics_group_different_words_with_the_same_meaning():
    assert candidate_topics(
        {"title": "A benchmark for model graders on private code"}
    )[0] == "agent-evals"
    assert candidate_topics(
        {"title": "Recursive agents improve through self-play"}
    )[0] == "self-improvement"
    assert candidate_topics({"title": "Private tests for model graders"})[0] == "agent-evals"


def test_candidate_topics_match_whole_phrases():
    assert candidate_topics({"title": "Storage engines for databases"}) == ["other"]
    assert candidate_topics({"title": "Planting policies for gardens"}) == ["other"]
    assert candidate_topics({"title": "Automatic verse versification"}) == ["other"]
    assert candidate_topics({"title": "Clinical retrial design"}) == ["other"]
