"""Export a project's study-map topics as lesson packs for the built track.

Runs on the laptop: the video job in AWS cannot see local projects, and the
packs hold private code, so they go to the private results bucket and never
into this public repository.
Design: docs/superpowers/specs/2026-10-05-built-lane-design.md, section 2.

    uv run python scripts/export_study_topics.py ~/path/to/project \\
        --slug my-project --title "My Project"

AWS credentials must be current (AWS_PROFILE=agentlab on Daniel's laptop).
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from agentlab.source_text import extract_visible_text

GUIDE = Path("docs/study-guide.md")
BACKLOG_KEY = "topics/backlog.json"
FILE_CHARS = 12_000
PACK_CHARS = 120_000
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".lean", ".js", ".json", ".toml", ".yml", ".yaml", ".sh", ".txt", ".html"}
)
# A topic row: | **1. Name** | what to study | where it appears |
# Some guides escape the dot: **1\. Name**.
ROW_RE = re.compile(r"^\|\s*\*\*(\d+)\\?\.\s*(.+?)\*\*\s*\|(.*?)\|(.*?)\|\s*$")
TICK_RE = re.compile(r"`([^`\s]+\.[A-Za-z]+)`")
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
PAREN_RE = re.compile(r"\(([\w./\\-]+\.(?:py|md|js|lean|html))\)")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def parse_topics(guide_text: str) -> list[dict]:
    topics = []
    for line in guide_text.splitlines():
        match = ROW_RE.match(line.strip())
        if match:
            topics.append(
                {
                    "number": int(match[1]),
                    "topic": match[2].strip(),
                    "study": match[3].strip(),
                    "where": match[4].strip(),
                }
            )
    return topics


def resolve(name: str, roots: Sequence[Path]) -> Path | None:
    """A file the guide names, if it exists as text in one of the roots."""
    name = name.replace("\\_", "_")
    for root in roots:
        candidate = Path(name) if name.startswith("/") else root / name
        if candidate.is_file() and candidate.suffix in TEXT_SUFFIXES:
            return candidate
    return None


def read_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".html":
        text = extract_visible_text(text)
    return text[:FILE_CHARS]


def entry_points(guide_text: str, roots: Sequence[Path], guide_dir: Path) -> list[Path]:
    """The files the guide links to. Some guides link with absolute paths,
    others with paths relative to their own folder."""
    paths: list[Path] = []
    for name in LINK_RE.findall(guide_text) + PAREN_RE.findall(guide_text):
        if name.startswith(("http://", "https://", "#")):
            continue
        path = resolve(name, [guide_dir, *roots])
        if path:
            path = path.resolve()
            if path not in paths:
                paths.append(path)
    return paths


def build_pack(
    project_title: str,
    topic: dict,
    guide_text: str,
    roots: Sequence[Path],
    context: Sequence[Path],
) -> str:
    """The topic first, so the cut at PACK_CHARS drops shared context, not
    the topic's own files."""
    parts = [
        f"# Topic {topic['number']}: {topic['topic']} ({project_title})",
        f"## What to study\n{topic['study']}",
        f"## Where it appears\n{topic['where']}",
    ]
    for name in dict.fromkeys(TICK_RE.findall(topic["where"])):
        path = resolve(name, roots)
        if path:
            parts.append(f"## File: {name}\n{read_file(path)}")
    parts.append(f"## The whole study map\n{guide_text}")
    for path in context:
        parts.append(f"## Project file: {path.name}\n{read_file(path)}")
    return "\n\n".join(parts)[:PACK_CHARS]


def merge_backlog(backlog: list[dict], slug: str, entries: list[dict]) -> list[dict]:
    """Replace a project's entries in place, or append a new project."""
    kept = [entry for entry in backlog if entry["project"] != slug]
    first = next((i for i, entry in enumerate(backlog) if entry["project"] == slug), None)
    if first is None:
        return kept + entries
    index = sum(1 for entry in backlog[:first] if entry["project"] != slug)
    return kept[:index] + entries + kept[index:]


def build_export(
    project_dir: Path, slug: str, title: str, extra_dirs: Sequence[Path]
) -> tuple[list[dict], dict[str, str]]:
    roots = [project_dir, *extra_dirs]
    guide_text = (project_dir / GUIDE).read_text(encoding="utf-8")
    topics = parse_topics(guide_text)
    if not topics:
        raise SystemExit(f"no topic rows in {project_dir / GUIDE}")
    context = entry_points(guide_text, roots, (project_dir / GUIDE).parent)
    entries, packs = [], {}
    for topic in topics:
        topic_slug = f"{topic['number']:02d}-{slugify(topic['topic'])}"
        key = f"topics/{slug}/{topic_slug}.md"
        packs[key] = build_pack(title, topic, guide_text, roots, context)
        entries.append(
            {
                "project": slug,
                "project_title": title,
                "number": topic["number"],
                "topic": topic["topic"],
                "slug": topic_slug,
                "title": f"{topic['topic']} ({title})",
                "pack_key": key,
            }
        )
    return entries, packs


def write_local(out_dir: Path, slug: str, entries: list[dict], packs: dict[str, str]) -> Path:
    backlog_path = out_dir / BACKLOG_KEY
    backlog = json.loads(backlog_path.read_text(encoding="utf-8")) if backlog_path.exists() else []
    for key, text in packs.items():
        target = out_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    merged = merge_backlog(backlog, slug, entries)
    backlog_path.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    return backlog_path


def upload(s3_client, bucket: str, slug: str, entries: list[dict], packs: dict[str, str]) -> int:
    try:
        body = s3_client.get_object(Bucket=bucket, Key=BACKLOG_KEY)["Body"].read()
        backlog = json.loads(body)
    except s3_client.exceptions.NoSuchKey:
        backlog = []
    for key, text in packs.items():
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
    merged = merge_backlog(backlog, slug, entries)
    s3_client.put_object(
        Bucket=bucket,
        Key=BACKLOG_KEY,
        Body=json.dumps(merged, indent=1, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return len(merged)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--slug", required=True, help="short project id, e.g. my-project")
    parser.add_argument("--title", required=True, help="project name in video captions")
    parser.add_argument(
        "--extra-dir",
        type=Path,
        action="append",
        default=[],
        help="another folder the guide's paths may point into (repeatable)",
    )
    parser.add_argument("--bucket", default=os.environ.get("RESULTS_BUCKET"))
    parser.add_argument("--dry-run", type=Path, help="write packs here instead of S3")
    args = parser.parse_args(argv)

    project_dir = args.project_dir.expanduser().resolve()
    extra = [d.expanduser().resolve() for d in args.extra_dir]
    entries, packs = build_export(project_dir, args.slug, args.title, extra)
    if args.dry_run:
        path = write_local(args.dry_run, args.slug, entries, packs)
        print(f"{len(packs)} packs and the backlog written under {path.parent.parent}")
        return 0
    if not args.bucket:
        print("error: pass --bucket or set RESULTS_BUCKET", file=sys.stderr)
        return 2
    import boto3

    total = upload(boto3.client("s3"), args.bucket, args.slug, entries, packs)
    print(f"{len(packs)} packs uploaded; the backlog now holds {total} topics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
