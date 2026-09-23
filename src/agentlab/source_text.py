"""Turn a fetched page into the text the deep-read model actually reads.

Two jobs, in order:

1. `extract_visible_text` drops the markup. A fetched page is raw HTTP body,
   so script, style and tag soup can outweigh the prose by 5x to 70x
   (measured across 14 real picks on 2026-09-23: raw median 104k tokens,
   visible-text median 16k).
2. `cap_for_model` puts a hard ceiling on what is left. On 2026-09-23 the
   core track picked an HN page that weighed 2,258,480 tokens and Bedrock
   rejected the whole run with "prompt is too long ... 1000000 maximum";
   the track died and Daniel got an error ping instead of a video.

`MAX_SOURCE_TOKENS` is set from the real corpus, not from the model limit:
visible text across those 14 picks ran to 78k tokens at the very worst and
46k for the largest paper, so 80k keeps every real source whole and only
bites on pages that were never a paper to begin with.

The truncation is never silent. The model is told in the text itself, so it
can say the digest covers only part of the source rather than guessing.

Style follows scene_plan.py: no module-level litellm import, and the token
counter is injectable so tests run offline.
"""

import re

from bs4 import BeautifulSoup

MAX_SOURCE_TOKENS = 80_000
"""Ceiling on the source text handed to the deep-read model, in that model's
own tokens. 80k against a 1M window leaves the whole context free for the
system prompt, the digest and the scene plan, and caps one deep read at
roughly 0.24 USD of input on Sonnet 4.6."""

TOKENIZER_SAFETY = 1.30
"""The deployed model counts more tokens than the local counter does. The
same 1.30 factor costs.py documents for the newer Claude tokenizers, and
close to the 1.27 measured on 2026-09-23 between the Bedrock count for the
failing page (2,258,480) and the local count for the same page (1,781,213).
Applied as a margin, not as a precise conversion."""

FALLBACK_CHARS_PER_TOKEN = 2.5
"""Used only when the token counter is unavailable. The lowest ratio seen
across the 2026-09-23 corpus was 2.72 characters per token, so 2.5 keeps the
fallback on the safe side of every real source."""

PRE_SLICE_CHARS_PER_TOKEN = 5.0
"""A first cut by character count, so a multi-megabyte page is never handed
to the tokenizer whole. Deliberately generous: the highest ratio measured was
4.47 characters per token, so this cut can only remove text that was already
far past the budget."""

TRUNCATION_NOTICE = (
    "\n\n[The source was truncated here to fit the model context window. "
    "Everything above is the start of the source. Say in the digest that it "
    "covers only the part you were given.]"
)

_BLOCK_TAGS = (
    "p",
    "div",
    "section",
    "article",
    "li",
    "tr",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "figcaption",
)
_DROP_TAGS = ("script", "style", "noscript", "svg", "iframe")


def extract_visible_text(html: str) -> str:
    """The prose a reader would see, with script/style and markup removed.

    Block-level tags get an explicit newline before the text is flattened, so
    paragraphs and table rows survive as lines. Inline tags are joined with a
    space rather than nothing: merging them would glue words together.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()
    for tag in soup.find_all(list(_BLOCK_TAGS)):
        tag.append("\n")
    text = soup.get_text(" ")
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def count_tokens(text: str) -> int:
    """Local token count for `text`. Module-level so tests monkeypatch it."""
    import litellm

    return litellm.token_counter(model="claude-3-5-sonnet-20241022", text=text)


def cap_for_model(
    text: str,
    max_tokens: int = MAX_SOURCE_TOKENS,
    counter=None,
) -> str:
    """Return `text` unchanged, or truncated with `TRUNCATION_NOTICE` appended.

    The ceiling is `max_tokens` in the deployed model's tokens, which is
    `TOKENIZER_SAFETY` times what the local counter reports. A counter failure
    must never lose the track, so it degrades to a character cut.
    """
    # Resolved at call time, so monkeypatching count_tokens works.
    counter = counter or count_tokens
    local_budget = max_tokens / TOKENIZER_SAFETY

    pre_slice = int(local_budget * PRE_SLICE_CHARS_PER_TOKEN)
    truncated = len(text) > pre_slice
    body = text[:pre_slice]

    try:
        tokens = counter(body)
        # A ratio cut can overshoot on uneven text, so re-measure; three
        # passes is plenty at these sizes and bounds the work either way.
        for _ in range(3):
            if tokens <= local_budget:
                break
            keep = max(1, int(len(body) * local_budget / tokens))
            body = body[:keep]
            truncated = True
            tokens = counter(body)
    except Exception:  # noqa: BLE001 - a counter failure must not sink the track
        keep = int(local_budget * FALLBACK_CHARS_PER_TOKEN)
        if len(body) > keep:
            body = body[:keep]
            truncated = True

    if not truncated:
        return text
    return body + TRUNCATION_NOTICE


def prepare_source(
    html: str,
    max_tokens: int = MAX_SOURCE_TOKENS,
    counter=None,
) -> str:
    """The full path from fetched page to prompt-ready source text."""
    return cap_for_model(
        extract_visible_text(html), max_tokens=max_tokens, counter=counter
    )
