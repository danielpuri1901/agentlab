"""Video render pipeline: Polly narration, burned-in captions, Manim template.

This module never imports manim; manim runs in a separate uvx-managed
environment (see the docstring at the top of video_scenes.py) and is
invoked as a subprocess. Everything here that talks to the outside world
(ffprobe, ffmpeg, the `uvx manim` subprocess, Polly) goes through the two
seams `run_subprocess` and the injected `polly_client`, so every function
except `render_scene_video` (and its caller `render_video`) is testable
without manim, ffmpeg, or AWS installed.

House style (docs/specs/2026-08-23-daily-paper-videos.md stage 5, and
scripts/videos/tournament_001_cliff.py as the reference): dark background,
one accent color, step-by-step mechanism build with arrows, every text
block inside safe margins. The model never writes animation code; a bad
model day yields a boring video, never a broken one, because video_scenes.py
only ever reads a validated ScenePlan plus a list of numbers (durations)
plus the matching narration texts (captions).

Concat pattern: render the whole scene as ONE Manim video, timed segment by
segment from `durations` (one per narration clip) so the video's total
length matches the narration exactly; separately concat the per-clip mp3s
into one narration track, padding each clip with silence up to its target
length first (never shorter than the clip itself) when the caller has a
durations list that ran longer than the narration; mux that track onto the
video with a plain video+audio mux (no ffmpeg video filter). The render
subprocess receives a filtered environment and a hard timeout, since the
scene file it renders may be model-written. Filtering out inherited AWS
environment variables reduces direct exposure, but the subprocess retains
the worker's filesystem, user identity, and network access. It is not a
sandbox or a credential-isolation boundary. Captions are burned in by
video_scenes.py itself, not by
ffmpeg: the target environment's ffmpeg build lacks libass (`ffmpeg
-filters` has no `subtitles` entry), so the `-vf subtitles=...` approach
fails there with "Filter not found". A plain .srt is still generated as a
sidecar artifact (for future use, e.g. platform upload), but nothing in the
render path depends on ffmpeg being able to render it.
"""

import json
import os
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

from agentlab.scene_plan import KeyNumber, ScenePlan

ACCENT = "#2f6fd6"  # keep in sync with video_scenes.py's ACCENT constant
VIDEO_SCENES_FILE = Path(__file__).with_name("video_scenes.py")
SCENE_CLASS = "PaperScene"

PREFERRED_VOICE_LANGS = ("en-US", "en-GB")

SRT_WRAP_WIDTH = 42

DEFAULT_RENDER_TIMEOUT_SECONDS = 480
# Only these parent environment keys are copied into the render subprocess.
# This reduces direct credential exposure, but does not isolate the subprocess
# from the worker filesystem, user identity, or network.
RENDER_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


@dataclass(frozen=True)
class NarrationClip:
    path: Path
    seconds: float
    text: str


def run_subprocess(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Single seam for every external process this module shells out to
    (ffprobe, ffmpeg, `uvx manim`); tests monkeypatch this one function."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


# ---------------------------------------------------------------------------
# Scene text: the single source of truth for narration order. video_scenes.py
# does not import this (it must run standalone under `uvx manim`, a separate
# environment from the main project); it derives the matching segment count
# from the plan dict itself. Keep the two in sync if this ordering changes.
# ---------------------------------------------------------------------------


def _numbers_text(key_numbers: list[KeyNumber]) -> str:
    parts = [f"{kn.value}: {kn.meaning}" for kn in key_numbers]
    return "Some numbers. " + " ".join(parts)


def scene_texts(plan: ScenePlan) -> list[str]:
    """Narration text, one entry per scene, in the order the video plays
    them: title+claim, each mechanism step, numbers (only if any), caveat,
    question."""
    texts = [f"{plan.title}. {plan.one_line_claim}"]
    texts += [step.narration for step in plan.mechanism_steps]
    if plan.key_numbers:
        texts.append(_numbers_text(plan.key_numbers))
    texts.append(plan.limits_or_caveats)
    texts.append(plan.street_test_question)
    return texts


def default_scene_durations(
    plan: ScenePlan,
    words_per_second: float = 2.4,
    min_seconds: float = 3.0,
    padding: float = 1.4,
) -> list[float]:
    """Duration estimate for each scene when no narration audio exists yet
    (used by the render-only path, e.g. the render-marked test)."""
    return [
        max(min_seconds, len(text.split()) / words_per_second + padding)
        for text in scene_texts(plan)
    ]


# ---------------------------------------------------------------------------
# Polly
# ---------------------------------------------------------------------------


def verify_voice(polly_client) -> str:
    """DescribeVoices; prefer a neural en-US/en-GB voice available in this
    region, else standard. This is the build-time Polly verification the
    spec requires (voice availability differs by AWS region)."""
    response = polly_client.describe_voices()
    candidates = [
        v
        for v in response.get("Voices", [])
        if v["LanguageCode"] in PREFERRED_VOICE_LANGS
    ]
    if not candidates:
        raise RuntimeError("no en-US or en-GB Polly voice available in this region")

    def rank(voice: dict) -> tuple:
        lang_rank = PREFERRED_VOICE_LANGS.index(voice["LanguageCode"])
        neural_rank = 0 if "neural" in voice.get("SupportedEngines", []) else 1
        return (lang_rank, neural_rank, voice["Id"])

    candidates.sort(key=rank)
    return candidates[0]["Id"]


def ffprobe_duration(path: Path) -> float:
    """Clip duration in seconds via ffprobe; module-level indirection so
    tests monkeypatch this instead of running real ffprobe on fake audio."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(path),
    ]
    result = run_subprocess(cmd)
    return float(result.stdout.strip())


def narrate(
    polly_client, texts: list[str], voice_id: str, out_dir
) -> list[NarrationClip]:
    """One mp3 per text, duration measured with ffprobe. The template path
    passes scene_texts(plan); the story path passes each beat's narration.
    `voice_id` is expected to be neural-capable (verify_voice's job);
    Engine="neural" is requested explicitly since Polly does not infer it
    from the voice."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, text in enumerate(texts):
        response = polly_client.synthesize_speech(
            Text=text, OutputFormat="mp3", VoiceId=voice_id, Engine="neural"
        )
        audio_path = out_dir / f"clip_{i:02d}.mp3"
        audio_path.write_bytes(response["AudioStream"].read())
        clips.append(
            NarrationClip(
                path=audio_path, seconds=ffprobe_duration(audio_path), text=text
            )
        )
    return clips


# ---------------------------------------------------------------------------
# Subtitles
# ---------------------------------------------------------------------------


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(clips: list[NarrationClip], durations: list[float] | None = None) -> str:
    """One .srt from cumulative timings; exact, since these are the same
    texts Polly spoke, not a transcription. Pass `durations` (e.g. the
    padded target_seconds also given to concat_audio) when the clips'
    own seconds no longer match the final audio track's timing."""
    lengths = durations if durations is not None else [c.seconds for c in clips]
    if len(lengths) != len(clips):
        raise ValueError(
            f"durations has {len(lengths)} entries, clips has {len(clips)}"
        )
    entries = []
    cursor = 0.0
    for i, (clip, length) in enumerate(zip(clips, lengths, strict=True), start=1):
        start, end = cursor, cursor + length
        cursor = end
        wrapped = "\n".join(textwrap.wrap(clip.text, SRT_WRAP_WIDTH)) or clip.text
        entries.append(
            f"{i}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n{wrapped}\n"
        )
    return "\n".join(entries)


# ---------------------------------------------------------------------------
# Manim render (the one function in this module that needs manim, via uvx)
# ---------------------------------------------------------------------------


def _manim_command() -> list[str]:
    """Pick the manim invocation for this environment.

    The video image installs manim in the project venv (Dockerfile.video's
    `--group video`), so run it in-process via the current interpreter; on
    Daniel's laptop the main venv deliberately has no manim (dependency
    conflict), so fall back to the uvx-isolated install. The live failure
    this guards against: uvx inside the container tried to resolve manim
    from PyPI at runtime and died (explain run 2026-08-25, all 3 tracks).
    """
    import importlib.util
    import sys

    if importlib.util.find_spec("manim") is not None:
        return [sys.executable, "-m", "manim"]
    return ["uvx", "--python", "3.12", "manim"]


def render_env(scene_dir: Path, spec_path: Path, extra_env: dict | None = None) -> dict:
    """Build the filtered render environment.

    Only RENDER_ENV_KEYS are copied from the parent, then the scene directory,
    spec path, and extra values are added. This filtering reduces inherited
    secret exposure but does not provide filesystem, identity, network, or
    credential isolation.
    """
    env = {key: os.environ[key] for key in RENDER_ENV_KEYS if key in os.environ}
    python_path = str(scene_dir)
    if os.environ.get("PYTHONPATH"):
        python_path = f"{python_path}{os.pathsep}{os.environ['PYTHONPATH']}"
    env["PYTHONPATH"] = python_path
    env["SCENE_SPEC_JSON"] = str(spec_path)
    if extra_env:
        env.update(extra_env)
    return env


def _write_scene_spec(spec: dict) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="agentlab-scene-spec-", delete=False
    ) as handle:
        json.dump(spec, handle)
        return Path(handle.name)


def _find_rendered_video(out_dir: Path, scene_class: str) -> Path:
    matches = sorted(Path(out_dir).glob(f"videos/*/*/{scene_class}.mp4"))
    if not matches:
        raise FileNotFoundError(f"no rendered {scene_class}.mp4 found under {out_dir}")
    return matches[-1]


def render_scene_video(
    scene_file,
    scene_class: str,
    spec: dict,
    out_dir,
    quality: str = "l",
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    extra_env: dict | None = None,
) -> Path:
    """Render a Manim scene through the subprocess seam.

    The subprocess gets a filtered environment, a hard timeout, and the spec
    as a JSON path in SCENE_SPEC_JSON. Raises subprocess.CalledProcessError on a
    manim failure and subprocess.TimeoutExpired on a timeout.
    Shells out to `uvx --python 3.12 manim`; see module docstring for why
    that has to be a subprocess rather than an import."""
    scene_file = Path(scene_file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = _write_scene_spec(spec)
    env = render_env(scene_file.parent, spec_path, extra_env)
    cmd = _manim_command() + [
        "render",
        f"-q{quality}",
        "--media_dir",
        str(out_dir),
        str(scene_file),
        scene_class,
    ]
    run_subprocess(cmd, env=env, timeout=timeout_seconds)
    return _find_rendered_video(out_dir, scene_class)


def template_spec(plan: ScenePlan, durations: list[float]) -> dict:
    """The scene spec for today's fixed PaperScene template: captions are
    `scene_texts(plan)` itself, so video_scenes.py never has to re-derive
    narration text from the plan structure."""
    texts = scene_texts(plan)
    if len(durations) != len(texts):
        raise ValueError(
            f"durations has {len(durations)} entries, plan needs {len(texts)}"
        )
    return {"plan": plan.model_dump(), "durations": durations, "captions": texts}


def render_template_video(
    plan: ScenePlan, durations: list[float], out_dir, quality: str = "l"
) -> Path:
    """Today's fixed PaperScene template, through the general render seam."""
    return render_scene_video(
        VIDEO_SCENES_FILE,
        SCENE_CLASS,
        template_spec(plan, durations),
        out_dir,
        quality=quality,
    )


def concat_audio(
    clips: list[NarrationClip],
    out_path: Path,
    target_seconds: list[float] | None = None,
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> Path:
    """Concatenate narration clips; with target_seconds, first pad each clip
    with silence to its target (never shorter than the clip itself), so the
    audio lines up with beats that ran a little longer than their narration."""
    if target_seconds is not None and len(target_seconds) != len(clips):
        raise ValueError(
            f"target_seconds has {len(target_seconds)} entries, clips has {len(clips)}"
        )
    inputs = []
    for clip in clips:
        inputs += ["-i", str(clip.path)]
    n = len(clips)
    if target_seconds is None:
        filter_str = (
            "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
        )
    else:
        pads = "".join(
            f"[{i}:a]apad=whole_dur={max(target, clip.seconds):.3f}[a{i}];"
            for i, (clip, target) in enumerate(zip(clips, target_seconds, strict=True))
        )
        filter_str = (
            pads + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
        )
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_str,
        "-map",
        "[out]",
        str(out_path),
    ]
    run_subprocess(cmd, timeout=timeout_seconds)
    return out_path


def mux_final(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> Path:
    """Plain video+audio mux, no filter graph: the silent video already has
    captions burned in by Manim, so nothing here needs to touch the video
    stream, and `-c:v copy` just repackages it (no re-encode, no libass
    dependency)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(out_path),
    ]
    run_subprocess(cmd, timeout=timeout_seconds)
    return out_path


def render_video(
    plan: ScenePlan, clips: list[NarrationClip], out_path
) -> tuple[Path, Path]:
    """Full pipeline: render the scene (captions burned in, sized to the
    narration), concat the narration clips, mux. Returns (video_path,
    srt_path); the .srt is a sidecar artifact only (e.g. for a future
    platform upload) -- nothing in this render path depends on it."""
    out_path = Path(out_path)
    work_dir = Path(tempfile.mkdtemp(prefix="agentlab-video-"))
    durations = [clip.seconds for clip in clips]
    silent_video = render_template_video(
        plan, durations, work_dir / "render", quality="m"
    )
    audio_path = concat_audio(clips, work_dir / "narration.mp3")
    srt_path = out_path.with_suffix(".srt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(build_srt(clips), encoding="utf-8")
    video_path = mux_final(silent_video, audio_path, out_path)
    return video_path, srt_path
