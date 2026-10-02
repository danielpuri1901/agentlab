"""Daniel's taste profile: versioned text in S3 behind one DynamoDB pointer.

The profile is semantic memory. `docs/interests.md` is baked into the
images, so nothing can update it at runtime; the live profile lives in S3
and the pointer item on the state table names the current version. The
pointer keeps the previous version so the REVERT tap in the approvals
Lambda can flip back without copying any object. The Lambda re-implements
the pointer read and flip with stdlib + boto3 (it imports nothing from this
package), so the item shape here is a contract: change both or neither.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from botocore.exceptions import ClientError

POINTER_KEY = {"experiment_id": "profile#current", "sk": "profile"}
POLICY_VERSION = "taste-profile-v1"
VERSION_FORMAT = "%Y%m%dT%H%M%SZ"
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SECTIONS = ("Prefer", "Avoid", "Positive examples", "Negative examples", "Source weights")
EXAMPLE_SECTIONS = ("Positive examples", "Negative examples")
MAX_WORDS = 1200
MIN_BULLETS = 3
MAX_EXAMPLES = 10
MIN_EXAMPLES = 3
TITLE_MATCH = 0.86
TITLE_LINE = "# Daniel's taste profile"


@dataclass(frozen=True)
class ProfilePointer:
    version: str
    s3_key: str
    previous_version: str | None
    previous_s3_key: str | None
    applied_ts: str
    source: str
    eval: dict


def version_now(now: datetime) -> str:
    return now.strftime(VERSION_FORMAT)


def profile_key(version: str) -> str:
    return f"profile/{version}.md"


def candidate_key(version: str) -> str:
    return f"profile/candidates/{version}.md"


def get_pointer(table) -> ProfilePointer | None:
    item = table.get_item(Key=POINTER_KEY).get("Item")
    if not item:
        return None
    raw_eval = item.get("eval") or "{}"
    try:
        eval_result = json.loads(raw_eval) if isinstance(raw_eval, str) else dict(raw_eval)
    except json.JSONDecodeError:
        eval_result = {}
    return ProfilePointer(
        version=str(item["version"]),
        s3_key=str(item["s3_key"]),
        previous_version=item.get("previous_version") or None,
        previous_s3_key=item.get("previous_s3_key") or None,
        applied_ts=str(item.get("applied_ts") or ""),
        source=str(item.get("source") or ""),
        eval=eval_result,
    )


def set_pointer(
    table,
    version: str,
    s3_key: str,
    previous: ProfilePointer | None,
    source: str,
    eval_result: dict,
    now: datetime,
) -> ProfilePointer:
    pointer = ProfilePointer(
        version=version,
        s3_key=s3_key,
        previous_version=previous.version if previous else None,
        previous_s3_key=previous.s3_key if previous else None,
        applied_ts=now.strftime(TS_FORMAT),
        source=source,
        eval=eval_result,
    )
    table.put_item(
        Item={
            **POINTER_KEY,
            "version": pointer.version,
            "s3_key": pointer.s3_key,
            "previous_version": pointer.previous_version,
            "previous_s3_key": pointer.previous_s3_key,
            "applied_ts": pointer.applied_ts,
            "source": pointer.source,
            "eval": json.dumps(pointer.eval),
        }
    )
    return pointer


def write_profile(s3_client, bucket: str, key: str, text: str) -> None:
    s3_client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))


def load_profile_text(table, s3_client, bucket: str, fallback_path: str | Path) -> tuple[str, str]:
    """The current profile text and its version, or the seed file and "seed"."""
    pointer = get_pointer(table)
    if pointer is not None:
        try:
            body = s3_client.get_object(Bucket=bucket, Key=pointer.s3_key)["Body"].read()
            return body.decode("utf-8"), pointer.version
        except ClientError:
            pass
    return Path(fallback_path).read_text(encoding="utf-8"), "seed"


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$")


def _strip_fences(text: str) -> str:
    lines = [line for line in text.splitlines() if not _FENCE_RE.match(line.strip())]
    return "\n".join(lines)


def split_sections(text: str) -> dict[str, str]:
    """Map each `## Heading` to the lines under it, in document order."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in _strip_fences(text).splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(body).strip()
            current = line[3:].strip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        sections[current] = "\n".join(body).strip()
    return sections


def bullets(body: str) -> list[str]:
    return [line[2:].strip() for line in body.splitlines() if line.startswith("- ")]


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _known_title(line: str, train_titles: list[str]) -> bool:
    candidate = _plain(line.split(" (", 1)[0])
    return any(
        SequenceMatcher(None, candidate, _plain(title)).ratio() >= TITLE_MATCH
        for title in train_titles
    )


def validate_profile(text: str, train_titles: list[str]) -> tuple[dict[str, str] | None, list[str]]:
    """Cleaned sections keyed by heading, or None with the list of problems."""
    problems: list[str] = []
    text = text.replace("\u2014", "-").replace("\u2013", "-")  # no em or en dashes on Daniel's phone
    sections = split_sections(text)
    for heading in SECTIONS:
        if heading not in sections:
            problems.append(f"missing section: {heading}")
    if problems:
        return None, problems
    if list(sections)[: len(SECTIONS)] != list(SECTIONS):
        problems.append("sections out of order")
    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"too many words: {words} > {MAX_WORDS}")
    cleaned: dict[str, str] = {}
    for heading in ("Prefer", "Avoid"):
        lines = bullets(sections[heading])
        if len(lines) < MIN_BULLETS:
            problems.append(f"{heading}: {len(lines)} bullets, need {MIN_BULLETS}")
        cleaned[heading] = "\n".join(f"- {line}" for line in lines)
    for heading in EXAMPLE_SECTIONS:
        kept = [line for line in bullets(sections[heading]) if _known_title(line, train_titles)]
        kept = kept[:MAX_EXAMPLES]
        if len(kept) < MIN_EXAMPLES:
            problems.append(f"{heading}: {len(kept)} known examples, need {MIN_EXAMPLES}")
        cleaned[heading] = "\n".join(f"- {line}" for line in kept)
    cleaned["Source weights"] = sections["Source weights"]
    if problems:
        return None, problems
    return cleaned, []


def render_profile(version: str, sections: dict[str, str]) -> str:
    parts = [TITLE_LINE, f"version: {version}", ""]
    for heading in SECTIONS:
        parts.append(f"## {heading}")
        parts.append(sections.get(heading, "").strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def source_weights_section(stats: dict) -> str:
    lines = []
    for name in sorted(stats.get("sources", {})):
        counts = stats["sources"][name]
        if not counts.get("decided"):
            lines.append(f"- {name}: no decisions yet")
            continue
        lines.append(
            f"- {name}: approval {counts['approval_rate']:.2f} "
            f"({counts['approved']} of {counts['decided']})"
        )
    return "\n".join(lines)


def diff_bullets(old_text: str, new_text: str) -> dict[str, dict[str, list[str]]]:
    old_sections = split_sections(old_text)
    new_sections = split_sections(new_text)
    diff: dict[str, dict[str, list[str]]] = {}
    for heading in ("Prefer", "Avoid"):
        old_lines = bullets(old_sections.get(heading, ""))
        new_lines = bullets(new_sections.get(heading, ""))
        diff[heading] = {
            "added": [line for line in new_lines if line not in old_lines],
            "removed": [line for line in old_lines if line not in new_lines],
        }
    return diff
