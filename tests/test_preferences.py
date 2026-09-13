from agentlab.preferences import (
    build_preference_context,
    candidate_topics,
    load_live_feedback,
    merge_feedback,
    normalise_topic,
)


def _item(title: str, rating: str, topics: list[str] | None = None) -> dict:
    item = {"title": title, "rating": rating}
    if topics is not None:
        item["topics"] = topics
    return item


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


def test_preference_context_stays_off_with_too_few_total_labels():
    items = [_item(f"Agent benchmark {i}", "COOL", ["agent-evals"]) for i in range(29)]
    assert build_preference_context(items) == ""


def test_preference_context_stays_off_without_enough_negative_signal():
    items = [_item(f"Agent benchmark {i}", "COOL", ["agent-evals"]) for i in range(21)]
    items += [_item(f"Other paper {i}", "MEH", ["other"]) for i in range(9)]
    assert build_preference_context(items) == ""


def test_preference_context_requires_enough_cases_in_the_same_topic():
    items = [_item(f"Liked eval {i}", "COOL", ["agent-evals"]) for i in range(7)]
    items += [_item(f"Liked memory {i}", "COOL", ["agent-memory"]) for i in range(13)]
    items += [_item(f"Skipped media {i}", "SKIP", ["media-generation"]) for i in range(10)]
    context = build_preference_context(items)
    assert "agent-evals" not in context
    assert "agent-memory" in context
    assert "media-generation" in context


def test_preference_context_uses_partial_pooling_and_reports_policy_version():
    items = [_item(f"Liked eval {i}", "COOL", ["agent-evals"]) for i in range(10)]
    items += [_item(f"Neutral systems {i}", "MEH", ["agent-systems"]) for i in range(10)]
    items += [_item(f"Skipped media {i}", "SKIP", ["media-generation"]) for i in range(10)]
    context = build_preference_context(items)
    assert "topic-feedback-v1" in context
    assert "agent-evals: prefer" in context
    assert "media-generation: avoid" in context
    assert "10 labels" in context
    assert "tie-breaker" in context


def test_live_rating_replaces_fuzzy_matching_golden_title():
    golden = [_item("LLM as a Judge", "COOL", ["agent-evals"])]
    live = [_item("LLM-as-a-Judge", "SKIP", ["agent-evals"])]
    merged = merge_feedback(golden, live)
    assert merged == live


def test_live_feedback_reads_every_scan_page():
    class Table:
        def __init__(self):
            self.calls = []

        def scan(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {
                    "Items": [_item("First", "COOL")],
                    "LastEvaluatedKey": {"experiment_id": "video#1", "sk": "video"},
                }
            return {"Items": [_item("Second", "SKIP")]}

    table = Table()
    assert [item["title"] for item in load_live_feedback(table)] == ["First", "Second"]
    assert table.calls[1]["ExclusiveStartKey"]["experiment_id"] == "video#1"
