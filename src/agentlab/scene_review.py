"""Independent review of a finished video's stills (docs/superpowers/specs/
2026-10-23-chapter-lanes-design.md, section 7).

The reviewer sees one contact sheet per chapter and the storyboard text of
that chapter, never the code. It names at most one chapter with a problem a
viewer would notice, as a structured verdict the worker can act on. Every
failure of the reviewer itself counts as "ship": it can delay a video, never
block it.
"""
import base64
import logging
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

REVIEW_SYSTEM = (
    "You review stills from a short explainer video before it is sent. Each chapter gives you "
    "one contact sheet (three frames per beat, left to right, top to bottom) and the words the "
    "viewer hears and reads in that chapter. Judge what a viewer would notice: unreadable or "
    "overlapping text, an empty or near-empty stage for a whole beat, a drawing that shows "
    "something other than what the narration describes, text or shapes cut off at the frame "
    "edge. Ignore style and taste. Reply with the verdict object only: verdict \"ship\" when "
    "every chapter is acceptable; verdict \"fix\" with the single worst chapter number and one "
    "sentence naming the problem a viewer would say, when one chapter needs a fix. Never name "
    "more than one chapter."
)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ship", "fix"]},
        "chapter": {"type": ["integer", "null"]},
        "problem": {"type": "string"},
    },
    "required": ["verdict", "chapter", "problem"],
    "additionalProperties": False,
}


class Verdict(BaseModel):
    verdict: Literal["ship", "fix"]
    chapter: int | None = None
    problem: str = ""


def _chapter_text(k: int, board) -> str:
    lines = [f"Chapter {k}:"]
    for beat in board.beats:
        labels = ", ".join(beat.on_screen_text) or "none"
        lines.append(f"- hears: {beat.narration} | reads: {labels}")
    return "\n".join(lines)


def review(sheets: list[bytes], chapter_boards: list, complete, model: str, *,
           max_tokens: int = 300, timeout: int = 60) -> Verdict:
    # The request is built inside the try too: the worker calls this without a
    # guard, so nothing here may raise.
    try:
        content: list[dict] = []
        for k, (png, board) in enumerate(zip(sheets, chapter_boards, strict=True), start=1):
            content.append({"type": "text", "text": _chapter_text(k, board)})
            url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url}})
        content.append({"type": "text", "text": "Reply with the verdict object."})
        messages = [{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": content}]
        raw = complete(
            model, messages, max_tokens=max_tokens, timeout=timeout,
            extra_fields={"output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}}},
        )
        verdict = Verdict.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - the reviewer never blocks a video
        logger.warning("reviewer failed, shipping without a verdict: %s", exc)
        return Verdict(verdict="ship", problem=f"reviewer failed: {type(exc).__name__}")
    if verdict.verdict == "fix" and not (verdict.chapter and 1 <= verdict.chapter <= len(sheets)):
        return Verdict(verdict="ship", problem="reviewer named no valid chapter")
    return verdict
