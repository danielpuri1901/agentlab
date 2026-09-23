"""Source-text preparation tests, fully offline: the token counter is a fake
injected by each test, mirroring how tests/test_scene_plan.py injects
`complete` and `fetch_text`. No network, no litellm.
"""

from agentlab.source_text import (
    TRUNCATION_NOTICE,
    cap_for_model,
    extract_visible_text,
    prepare_source,
)

HTML = (
    "<html><head><title>Paper</title>"
    "<style>.a{margin:0.5em}</style>"
    "<script>var x = 12345;</script></head>"
    "<body><p>The study reports a 22.9% gain.</p>"
    "<p>It tests 50 simulated turns.</p></body></html>"
)


def _counter(chars_per_token: float):
    """A deterministic stand-in for the real tokenizer."""
    return lambda text: max(1, int(len(text) / chars_per_token))


def test_extract_visible_text_keeps_prose():
    text = extract_visible_text(HTML)

    assert "The study reports a 22.9% gain." in text
    assert "It tests 50 simulated turns." in text


def test_extract_visible_text_drops_script_and_style():
    text = extract_visible_text(HTML)

    assert "var x" not in text
    assert "12345" not in text
    assert "margin" not in text


def test_cap_for_model_leaves_a_small_source_untouched():
    text = "a" * 400

    assert cap_for_model(text, max_tokens=1000, counter=_counter(4)) == text


def test_cap_for_model_truncates_an_oversized_source():
    text = "a" * 400_000

    capped = cap_for_model(text, max_tokens=1_000, counter=_counter(4))

    assert capped.endswith(TRUNCATION_NOTICE)
    assert len(capped) < len(text)


def test_cap_for_model_truncation_lands_under_the_budget():
    text = "a" * 400_000
    count = _counter(4)

    capped = cap_for_model(text, max_tokens=1_000, counter=count)

    body = capped[: -len(TRUNCATION_NOTICE)]
    assert count(body) <= 1_000


def test_cap_for_model_says_out_loud_that_it_truncated():
    text = "a" * 400_000

    capped = cap_for_model(text, max_tokens=1_000, counter=_counter(4))

    assert "truncated" in capped.lower()


def test_cap_for_model_falls_back_when_the_counter_fails():
    text = "a" * 400_000

    def broken_counter(_text):
        raise RuntimeError("tokenizer unavailable")

    capped = cap_for_model(text, max_tokens=1_000, counter=broken_counter)

    assert capped.endswith(TRUNCATION_NOTICE)
    assert len(capped) < len(text)


def test_prepare_source_strips_then_caps():
    html = "<body><p>" + ("word " * 200_000) + "</p></body>"

    prepared = prepare_source(html, max_tokens=1_000, counter=_counter(4))

    assert "<p>" not in prepared
    assert prepared.endswith(TRUNCATION_NOTICE)


def test_prepare_source_keeps_a_normal_paper_whole():
    prepared = prepare_source(HTML, max_tokens=80_000, counter=_counter(4))

    assert "22.9%" in prepared
    assert TRUNCATION_NOTICE not in prepared
