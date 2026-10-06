"""The built-lane export: study-map rows become lesson packs and a backlog."""

import importlib.util
import json
import sys
from pathlib import Path

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_SCRIPT = Path(__file__).parent.parent / "scripts" / "export_study_topics.py"
spec = importlib.util.spec_from_file_location("export_study_topics", _SCRIPT)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

LEAN_GUIDE = """# Study guide for the Lean proofs project

| Topic | What to study | Where it appears |
|---|---|---|
| **1. Lean 4: claims as types** | A claim is a type. | `solutions/problem.lean` and `challenge/problem.lean` (checker). |
| **2. Tactics and the proof state** | Goals and hypotheses. | `LESSONS.md` on `origin/deep/proof` |

Useful entry points in your project:

- [Write-up]({root}/docs/writeup.html): the story.
"""

SIM_GUIDE = """| Topic | What to study | Where it appears |
|---|---|---|
| **1\\. Scientific programming** | Python, NumPy arrays | Simulates many plans together |
| **10\\. Planning and control** | Model predictive control | Scores burn sequences |

- Powered physics (pilot\\_physics.py)
- [Full experiment explanation](pilot-article.md)
- [Planning and feedback control](../pilot_control.py)
"""


def _project(tmp_path, guide):
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "solutions").mkdir()
    (project / "solutions" / "problem.lean").write_text("theorem x : 1 < 2 := by decide\n")
    (project / "docs" / "writeup.html").write_text("<h1>Untrusted prover</h1><p>x &lt; y</p>")
    (project / "pilot_physics.py").write_text("def step(state):\n    return state\n")
    (project / "pilot_control.py").write_text("def plan(state):\n    return []\n")
    (project / "docs" / "pilot-article.md").write_text("# The pilot\n")
    (project / "docs" / "study-guide.md").write_text(guide.format(root=project), encoding="utf-8")
    checker = tmp_path / "checker"
    (checker / "challenge").mkdir(parents=True)
    (checker / "challenge" / "problem.lean").write_text("theorem frozen : True := sorry\n")
    return project, checker


def test_both_guide_formats_parse():
    lean = export.parse_topics(LEAN_GUIDE)
    sim = export.parse_topics(SIM_GUIDE)

    assert [(t["number"], t["topic"]) for t in lean] == [
        (1, "Lean 4: claims as types"),
        (2, "Tactics and the proof state"),
    ]
    assert [(t["number"], t["topic"]) for t in sim] == [
        (1, "Scientific programming"),
        (10, "Planning and control"),
    ]


def test_pack_holds_the_topic_its_files_and_the_guide(tmp_path):
    project, checker = _project(tmp_path, LEAN_GUIDE)

    entries, packs = export.build_export(project, "lean-proofs", "Lean Proofs", [checker])

    first = packs[entries[0]["pack_key"]]
    assert first.startswith("# Topic 1: Lean 4: claims as types (Lean Proofs)")
    assert "theorem x : 1 < 2" in first
    assert "theorem frozen : True := sorry" in first
    assert "## The whole study map" in first
    assert "Untrusted prover" in first and "x < y" in first
    assert entries[0] == {
        "project": "lean-proofs",
        "project_title": "Lean Proofs",
        "number": 1,
        "topic": "Lean 4: claims as types",
        "slug": "01-lean-4-claims-as-types",
        "title": "Lean 4: claims as types (Lean Proofs)",
        "pack_key": "topics/lean-proofs/01-lean-4-claims-as-types.md",
    }


def test_escaped_entry_points_resolve(tmp_path):
    project, _checker = _project(tmp_path, SIM_GUIDE)

    entries, packs = export.build_export(project, "flight-sim", "Flight Sim", [])

    pack = packs[entries[1]["pack_key"]]
    assert "def step(state)" in pack
    assert "def plan(state)" in pack
    assert "# The pilot" in pack


def test_files_and_packs_are_capped(tmp_path, monkeypatch):
    project, checker = _project(tmp_path, LEAN_GUIDE)
    (project / "solutions" / "problem.lean").write_text("a" * 50_000)

    _entries, packs = export.build_export(project, "lean-proofs", "Lean Proofs", [checker])
    pack = next(iter(packs.values()))
    assert "a" * export.FILE_CHARS in pack and "a" * (export.FILE_CHARS + 1) not in pack

    monkeypatch.setattr(export, "PACK_CHARS", 5_000)
    _entries, packs = export.build_export(project, "lean-proofs", "Lean Proofs", [checker])
    assert len(next(iter(packs.values()))) == 5_000


def test_merge_appends_a_new_project_and_replaces_one_in_place():
    lean = [{"project": "lean", "slug": "01"}, {"project": "lean", "slug": "02"}]
    hole = [{"project": "hole", "slug": "01"}]

    backlog = export.merge_backlog([], "lean", lean)
    backlog = export.merge_backlog(backlog, "hole", hole)
    assert [e["project"] for e in backlog] == ["lean", "lean", "hole"]

    redone = [{"project": "lean", "slug": "01"}]
    backlog = export.merge_backlog(backlog, "lean", redone)
    assert backlog == [{"project": "lean", "slug": "01"}, {"project": "hole", "slug": "01"}]


def test_dry_run_writes_packs_and_backlog(tmp_path):
    project, checker = _project(tmp_path, LEAN_GUIDE)
    out = tmp_path / "out"

    code = export.main(
        [str(project), "--slug", "lean-proofs", "--title", "Lean Proofs",
         "--extra-dir", str(checker), "--dry-run", str(out)]
    )

    assert code == 0
    backlog = json.loads((out / "topics" / "backlog.json").read_text(encoding="utf-8"))
    assert [e["slug"] for e in backlog] == ["01-lean-4-claims-as-types", "02-tactics-and-the-proof-state"]
    assert (out / backlog[0]["pack_key"]).exists()


def test_upload_merges_into_the_backlog_in_s3(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    project, checker = _project(tmp_path, LEAN_GUIDE)
    entries, packs = export.build_export(project, "lean-proofs", "Lean Proofs", [checker])

    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-west-1")
        s3.create_bucket(
            Bucket="results", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
        )
        s3.put_object(
            Bucket="results",
            Key="topics/backlog.json",
            Body=json.dumps([{"project": "other", "slug": "01"}]).encode(),
        )

        total = export.upload(s3, "results", "lean-proofs", entries, packs)

        backlog = json.loads(s3.get_object(Bucket="results", Key="topics/backlog.json")["Body"].read())
        pack = s3.get_object(Bucket="results", Key=entries[0]["pack_key"])["Body"].read().decode()

    assert total == 3
    assert [e["project"] for e in backlog] == ["other", "lean-proofs", "lean-proofs"]
    assert pack.startswith("# Topic 1:")
