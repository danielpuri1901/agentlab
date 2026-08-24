"""video_render.py tests, offline by default: Polly and every subprocess
call (ffprobe, ffmpeg, `uvx manim`) are monkeypatched through the module's
two seams (`run_subprocess`, `ffprobe_duration`), mirroring how
tests/test_scene_plan.py fakes `complete`. This module never imports
manim.

The single `@pytest.mark.render` test is the exception: it shells out for
real to `uvx manim` and ffprobe, rendering tests/fixtures/sample_plan.json
without audio, and is excluded from the default run by the marker
registered in pyproject.toml (`-m 'not render'`).
"""

import json
import subprocess
from pathlib import Path

import pytest

from agentlab import video_render
from agentlab.scene_plan import ScenePlan
from agentlab.video_render import NarrationClip

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_plan.json"


@pytest.fixture
def sample_plan() -> ScenePlan:
    return ScenePlan(**json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# scene_texts / default_scene_durations
# ---------------------------------------------------------------------------


def test_scene_texts_order_and_count(sample_plan):
    texts = video_render.scene_texts(sample_plan)
    n_steps = len(sample_plan.mechanism_steps)
    assert len(texts) == 1 + n_steps + 1 + 1 + 1  # title+claim, steps, numbers, caveat, question
    assert texts[0] == f"{sample_plan.title}. {sample_plan.one_line_claim}"
    for i, step in enumerate(sample_plan.mechanism_steps):
        assert texts[1 + i] == step.narration
    numbers_text = texts[1 + n_steps]
    assert numbers_text.startswith("Some numbers.")
    for kn in sample_plan.key_numbers:
        assert f"{kn.value}: {kn.meaning}" in numbers_text
    assert texts[-2] == sample_plan.limits_or_caveats
    assert texts[-1] == sample_plan.street_test_question


def test_scene_texts_skips_numbers_scene_when_absent(sample_plan):
    plan_without_numbers = sample_plan.model_copy(update={"key_numbers": []})
    texts = video_render.scene_texts(plan_without_numbers)
    n_steps = len(plan_without_numbers.mechanism_steps)
    assert len(texts) == 1 + n_steps + 1 + 1  # no numbers scene
    assert not any(t.startswith("Some numbers.") for t in texts)


def test_default_scene_durations_matches_scene_count_and_floor(sample_plan):
    durations = video_render.default_scene_durations(sample_plan)
    assert len(durations) == len(video_render.scene_texts(sample_plan))
    assert all(d >= 3.0 for d in durations)
    # The whole point of this fixture: a real render should clear 30s.
    assert sum(durations) > 30


def test_default_scene_durations_scales_with_word_count(sample_plan):
    durations = video_render.default_scene_durations(sample_plan)
    texts = video_render.scene_texts(sample_plan)
    longest_idx = max(range(len(texts)), key=lambda i: len(texts[i].split()))
    shortest_idx = min(range(len(texts)), key=lambda i: len(texts[i].split()))
    assert durations[longest_idx] >= durations[shortest_idx]


# ---------------------------------------------------------------------------
# verify_voice
# ---------------------------------------------------------------------------


class FakePolly:
    def __init__(self, voices, audio=b"\x00\x01"):
        self._voices = voices
        self._audio = audio
        self.synthesize_calls = []

    def describe_voices(self):
        return {"Voices": self._voices}

    def synthesize_speech(self, **kwargs):
        self.synthesize_calls.append(kwargs)

        class Stream:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"AudioStream": Stream(self._audio)}


def test_verify_voice_prefers_en_us_neural_over_standard_and_other_langs():
    polly = FakePolly(
        [
            {"Id": "Celine", "LanguageCode": "fr-FR", "SupportedEngines": ["neural"]},
            {"Id": "Salli", "LanguageCode": "en-US", "SupportedEngines": ["standard"]},
            {"Id": "Ivy", "LanguageCode": "en-US", "SupportedEngines": ["neural", "standard"]},
            {"Id": "Emma", "LanguageCode": "en-GB", "SupportedEngines": ["neural", "standard"]},
        ]
    )
    assert video_render.verify_voice(polly) == "Ivy"


def test_verify_voice_falls_back_to_standard_when_no_neural_voice_exists():
    polly = FakePolly([{"Id": "Salli", "LanguageCode": "en-US", "SupportedEngines": ["standard"]}])
    assert video_render.verify_voice(polly) == "Salli"


def test_verify_voice_raises_when_no_en_us_or_en_gb_voice():
    polly = FakePolly([{"Id": "Celine", "LanguageCode": "fr-FR", "SupportedEngines": ["neural"]}])
    with pytest.raises(RuntimeError):
        video_render.verify_voice(polly)


# ---------------------------------------------------------------------------
# ffprobe_duration
# ---------------------------------------------------------------------------


def test_ffprobe_duration_parses_subprocess_stdout(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="5.234000\n", stderr="")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    path = tmp_path / "clip.mp3"
    assert video_render.ffprobe_duration(path) == pytest.approx(5.234)
    assert captured["cmd"][0] == "ffprobe"
    assert str(path) in captured["cmd"]


# ---------------------------------------------------------------------------
# narrate
# ---------------------------------------------------------------------------


def test_narrate_returns_one_clip_per_scene_with_right_text_and_duration(
    sample_plan, tmp_path, monkeypatch
):
    monkeypatch.setattr(video_render, "ffprobe_duration", lambda path: 4.5)
    polly = FakePolly(
        [{"Id": "Ivy", "LanguageCode": "en-US", "SupportedEngines": ["neural"]}],
        audio=b"fake-mp3-bytes",
    )
    out_dir = tmp_path / "audio"
    clips = video_render.narrate(polly, sample_plan, "Ivy", out_dir)

    expected_texts = video_render.scene_texts(sample_plan)
    assert [c.text for c in clips] == expected_texts
    assert all(c.seconds == 4.5 for c in clips)
    assert all(c.path.exists() and c.path.read_bytes() == b"fake-mp3-bytes" for c in clips)
    assert len(polly.synthesize_calls) == len(expected_texts)
    assert all(call["VoiceId"] == "Ivy" for call in polly.synthesize_calls)
    assert all(call["Engine"] == "neural" for call in polly.synthesize_calls)
    assert [call["Text"] for call in polly.synthesize_calls] == expected_texts


def test_narrate_creates_out_dir_if_missing(sample_plan, tmp_path, monkeypatch):
    monkeypatch.setattr(video_render, "ffprobe_duration", lambda path: 3.0)
    polly = FakePolly([{"Id": "Ivy", "LanguageCode": "en-US", "SupportedEngines": ["neural"]}])
    out_dir = tmp_path / "does" / "not" / "exist" / "yet"
    assert not out_dir.exists()
    video_render.narrate(polly, sample_plan, "Ivy", out_dir)
    assert out_dir.exists()


# ---------------------------------------------------------------------------
# build_srt
# ---------------------------------------------------------------------------


def test_format_srt_timestamp():
    assert video_render._format_srt_timestamp(0) == "00:00:00,000"
    assert video_render._format_srt_timestamp(65.5) == "00:01:05,500"
    assert video_render._format_srt_timestamp(3661.001) == "01:01:01,001"


def test_build_srt_exact_timing_and_format():
    clips = [
        NarrationClip(path=Path("a.mp3"), seconds=1.5, text="Hello world"),
        NarrationClip(path=Path("b.mp3"), seconds=2.25, text="Second clip here"),
    ]
    expected = (
        "1\n00:00:00,000 --> 00:00:01,500\nHello world\n"
        "\n"
        "2\n00:00:01,500 --> 00:00:03,750\nSecond clip here\n"
    )
    assert video_render.build_srt(clips) == expected


def test_build_srt_wraps_long_text_at_wrap_width():
    import textwrap

    long_text = "This is a much longer narration line that should wrap across more than one subtitle row for readability."
    clips = [NarrationClip(path=Path("a.mp3"), seconds=4.0, text=long_text)]
    srt = video_render.build_srt(clips)
    expected_wrapped = "\n".join(textwrap.wrap(long_text, video_render.SRT_WRAP_WIDTH))
    assert expected_wrapped in srt
    assert srt.count("\n") >= expected_wrapped.count("\n") + 2  # index + timing lines too


# ---------------------------------------------------------------------------
# render_scene_video (the manim subprocess call)
# ---------------------------------------------------------------------------


def _fake_manim_run(media_dir: Path):
    def fake_run(cmd, **kwargs):
        out_file = media_dir / "videos" / "video_scenes" / "480p15" / "PaperScene.mp4"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def test_render_scene_video_builds_expected_manim_command(monkeypatch, tmp_path, sample_plan):
    calls = []
    media_dir = tmp_path / "out"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        return _fake_manim_run(media_dir)(cmd, **kwargs)

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    durations = video_render.default_scene_durations(sample_plan)

    result = video_render.render_scene_video(sample_plan, durations, media_dir, quality="l")

    assert result.name == "PaperScene.mp4"
    assert len(calls) == 1
    cmd, env = calls[0]
    assert cmd[:4] == ["uvx", "--python", "3.12", "manim"]
    assert "-ql" in cmd
    assert str(video_render.VIDEO_SCENES_FILE) in cmd
    assert video_render.SCENE_CLASS in cmd
    assert "--media_dir" in cmd
    assert str(media_dir) in cmd
    assert env is not None
    spec_path = Path(env["SCENE_SPEC_JSON"])
    assert spec_path.exists()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec["durations"] == durations
    assert spec["plan"]["title"] == sample_plan.title
    assert spec["plan"]["citation_url"] == sample_plan.citation_url
    # Captions are the narration text itself (scene_texts(plan)), so
    # video_scenes.py can burn them in without re-deriving anything from
    # the plan structure.
    assert spec["captions"] == video_render.scene_texts(sample_plan)


def test_render_scene_video_raises_on_duration_count_mismatch(sample_plan, tmp_path):
    with pytest.raises(ValueError):
        video_render.render_scene_video(sample_plan, [1.0], tmp_path)


# ---------------------------------------------------------------------------
# render_video (full pipeline orchestration)
# ---------------------------------------------------------------------------


def test_render_video_orchestrates_render_concat_mux(monkeypatch, tmp_path, sample_plan):
    texts = video_render.scene_texts(sample_plan)
    clips = []
    for i, text in enumerate(texts):
        path = tmp_path / f"clip_{i:02d}.mp3"
        path.write_bytes(b"fake")
        clips.append(NarrationClip(path=path, seconds=3.0 + i * 0.5, text=text))

    fake_silent_video = tmp_path / "silent.mp4"
    fake_silent_video.write_bytes(b"fake-video")

    render_calls = []

    def fake_render_scene_video(plan, durations, out_dir, quality="m"):
        render_calls.append((plan, durations, out_dir, quality))
        return fake_silent_video

    monkeypatch.setattr(video_render, "render_scene_video", fake_render_scene_video)

    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)

    out_path = tmp_path / "final" / "video.mp4"
    video_path, srt_path = video_render.render_video(sample_plan, clips, out_path)

    assert video_path == out_path

    # render_scene_video was called with durations pulled straight from clips
    assert len(render_calls) == 1
    _, durations, _, quality = render_calls[0]
    assert durations == [c.seconds for c in clips]
    assert quality == "m"

    assert len(run_calls) == 2
    concat_cmd, mux_cmd = run_calls

    assert concat_cmd[0] == "ffmpeg"
    for clip in clips:
        assert str(clip.path) in concat_cmd
    filter_arg = concat_cmd[concat_cmd.index("-filter_complex") + 1]
    assert f"concat=n={len(clips)}:v=0:a=1[out]" in filter_arg

    # Plain mux: no video filter at all (the target ffmpeg build has no
    # libass, so `-vf subtitles=...` is off the table -- captions are
    # burned in by Manim instead). -c:v copy since nothing touches video.
    assert mux_cmd[0] == "ffmpeg"
    assert "-vf" not in mux_cmd
    assert not any("subtitles=" in arg for arg in mux_cmd)
    assert str(fake_silent_video) in mux_cmd
    assert str(out_path) in mux_cmd
    assert mux_cmd[mux_cmd.index("-c:v") + 1] == "copy"

    # The .srt is still generated as a sidecar next to the video, unused by
    # ffmpeg but returned for future use.
    assert srt_path == out_path.with_suffix(".srt")
    assert srt_path.exists()
    assert srt_path.read_text(encoding="utf-8") == video_render.build_srt(clips)


# ---------------------------------------------------------------------------
# video_scenes.py pure helpers: caption wrapping, key-number parsing. These
# are extracted above video_scenes.py's `from manim import ...` (wrapped in
# try/except there) specifically so they're importable and testable here
# without manim installed -- caption presence in the actual render is
# otherwise only covered by the scene-spec-contains-narration assertions
# above and by the real render test at the bottom of this file.
# ---------------------------------------------------------------------------


def test_video_scenes_importable_with_or_without_manim():
    # The main venv has no manim (by design); the video image DOES. The
    # invariant is that the import succeeds and matches the environment,
    # not that manim is absent (this test runs inside both environments,
    # including the video image's build gate).
    import importlib.util

    from agentlab import video_scenes

    expected = importlib.util.find_spec("manim") is not None
    assert video_scenes._MANIM_AVAILABLE is expected


def test_caption_text_wraps_long_narration_at_caption_width():
    from agentlab.video_scenes import CAPTION_WRAP_WIDTH, caption_text

    long_text = (
        "Most self-improving systems run a bounded loop against a fixed "
        "external evaluator, not their own idea of what counts as better."
    )
    wrapped = caption_text(long_text)
    lines = wrapped.split("\n")
    assert len(lines) > 1
    assert all(len(line) <= CAPTION_WRAP_WIDTH for line in lines)
    assert wrapped.replace("\n", " ") == long_text


def test_caption_text_leaves_short_text_on_one_line():
    from agentlab.video_scenes import caption_text

    assert caption_text("Short caption.") == "Short caption."


def test_leading_number_parses_plain_and_suffixed_values():
    from agentlab.video_scenes import leading_number

    assert leading_number("1250") == (1250.0, "")
    assert leading_number("74%") == (74.0, "%")
    assert leading_number("1,250") == (1250.0, "")
    assert leading_number("-3.5x") == (-3.5, "x")


def test_leading_number_returns_none_for_non_numeric_value():
    from agentlab.video_scenes import leading_number

    assert leading_number("n/a") is None
    assert leading_number("~roughly") is None


def test_scene_spec_captions_feed_video_scenes_directly(sample_plan):
    # Round-trip: the exact strings render_scene_video writes into the spec
    # are what video_scenes.py's construct() would slice per segment and
    # hand to caption_mobject() -- verify the slicing math lines up with
    # scene_texts' segment ordering (title+claim, N steps, numbers, caveat,
    # question), the same ordering construct() assumes.
    from agentlab.video_scenes import caption_text

    captions = video_render.scene_texts(sample_plan)
    n_steps = len(sample_plan.mechanism_steps)
    expected = 1 + n_steps + 1 + 1 + 1
    assert len(captions) == expected

    mech_captions = captions[1 : 1 + n_steps]
    assert mech_captions == [step.narration for step in sample_plan.mechanism_steps]
    for text in captions:
        # caption_mobject() would call this on every entry; make sure none
        # of them error or produce an empty caption.
        assert caption_text(text)


# ---------------------------------------------------------------------------
# Real render: the one test that shells out to `uvx manim` and ffprobe.
# Excluded by default (pyproject.toml addopts = "-m 'not render'").
# ---------------------------------------------------------------------------


@pytest.mark.render
def test_render_scene_video_real_manim_output(tmp_path, sample_plan):
    durations = video_render.default_scene_durations(sample_plan)
    out_dir = tmp_path / "render"

    video_path = video_render.render_scene_video(sample_plan, durations, out_dir, quality="l")

    assert video_path.exists()
    duration = video_render.ffprobe_duration(video_path)
    assert duration > 30
