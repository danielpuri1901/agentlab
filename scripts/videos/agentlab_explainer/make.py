"""Render the AgentLab explainer video: narrate, render each chapter, mux, join.

Preview one chapter fast (no voice, durations estimated from word count,
480p, one PNG per beat end for layout checks):

    uv run python scripts/videos/agentlab_explainer/make.py --preview --chapter 3

Final render (Kokoro voice af_heart, 1080p30, every chapter, joined):

    uv run python scripts/videos/agentlab_explainer/make.py

Kokoro is open weights and runs locally; its model and voice pack live in
out/explainer/kokoro/ and its package runs in an isolated uv environment
(kokoro_tts.py). `--voice polly` uses the Polly generative voice instead
(needs AWS_PROFILE=agentlab). Outputs land in out/explainer/ at the repo
root. Voice clips are cached by text, so re-rendering a chapter never
synthesizes the same sentence twice. Manim runs from the uv cache with
--offline, so a render never downloads.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))

from narration import CHAPTERS, spoken

from agentlab.video_render import (
    NarrationClip,
    concat_audio,
    ffprobe_duration,
    mux_final,
)

OUT = REPO / "out" / "explainer"
CLIP_CACHE = OUT / "voice"
VOICES = {
    "kokoro": "af_heart",  # the only grade A voice in hexgrad/Kokoro-82M VOICES.md
    "polly": "Matthew",  # Polly generative engine
}
KOKORO_DIR = OUT / "kokoro"
KOKORO_SPEED = 1.0
POLLY_REGION = "eu-central-1"
WORDS_PER_SECOND = 2.6
BREATH_SECONDS = 0.35
CHAPTER_TAIL_SECONDS = 0.8
MANIM = ["uvx", "--offline", "--python", "3.12", "manim"]
SCENE_CLASS = "Chapter"


def narrate(texts: list[str], engine: str) -> list[NarrationClip]:
    """One cached clip per text; only missing clips are synthesized."""
    CLIP_CACHE.mkdir(parents=True, exist_ok=True)
    voice = VOICES[engine]
    suffix = ".wav" if engine == "kokoro" else ".mp3"
    paths, jobs = [], []
    for text in texts:
        said = spoken(text)
        key = hashlib.sha1(f"{engine}|{voice}|{said}".encode()).hexdigest()[:16]
        path = CLIP_CACHE / f"{key}{suffix}"
        paths.append(path)
        if not path.exists():
            jobs.append({"text": said, "path": str(path)})
    if jobs:
        (synthesize_kokoro if engine == "kokoro" else synthesize_polly)(jobs, voice)
    return [NarrationClip(path=path, seconds=ffprobe_duration(path), text=text) for path, text in zip(paths, texts, strict=True)]


def synthesize_kokoro(jobs: list[dict], voice: str) -> None:
    spec = OUT / "kokoro-jobs.json"
    spec.write_text(
        json.dumps(
            {
                "model": str(KOKORO_DIR / "kokoro-v1.0.onnx"),
                "voices": str(KOKORO_DIR / "voices-v1.0.bin"),
                "voice": voice,
                "speed": KOKORO_SPEED,
                "jobs": jobs,
            }
        ),
        encoding="utf-8",
    )
    cmd = ["uv", "run", "--isolated", "--no-project", "--python", "3.12", "--with", "kokoro-onnx==0.6.1"]
    subprocess.run([*cmd, "python", str(HERE / "kokoro_tts.py"), str(spec)], check=True)


def synthesize_polly(jobs: list[dict], voice: str) -> None:
    import boto3

    polly = boto3.client("polly", region_name=POLLY_REGION)
    for job in jobs:
        response = polly.synthesize_speech(Text=job["text"], OutputFormat="mp3", VoiceId=voice, Engine="generative")
        Path(job["path"]).write_bytes(response["AudioStream"].read())


def render(chapter: dict, durations: list[float], preview: bool) -> tuple[Path, dict]:
    number = chapter["number"]
    work = OUT / f"ch{number}-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for source in (HERE / "kit.py", REPO / "src" / "agentlab" / "story_scene.py", HERE / chapter["file"]):
        shutil.copy(source, work / source.name)
    spec = {
        "storyboard": {"beats": [{} for _ in chapter["beats"]]},
        "durations": durations,
        "captions": chapter["beats"],
        "chapter": number,
        "title": chapter["title"],
    }
    spec_path, timing_path = work / "spec.json", work / "timing.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(work), "SCENE_SPEC_JSON": str(spec_path), "SCENE_TIMING_OUT": str(timing_path)}
    quality = ["-ql"] if preview else ["-qh", "--frame_rate", "30"]
    cmd = MANIM + ["render", *quality, "--media_dir", str(work / "media"), str(work / chapter["file"]), SCENE_CLASS]
    subprocess.run(cmd, env=env, cwd=work, check=True)
    video = max((work / "media").glob(f"videos/*/*/{SCENE_CLASS}.mp4"))
    return video, json.loads(timing_path.read_text(encoding="utf-8"))


def beat_frames(video: Path, timing: dict, number: int) -> list[Path]:
    """One PNG at the end of every beat, the frame the layout must survive."""
    frames = []
    for beat in timing["beats"]:
        path = OUT / "frames" / f"ch{number}-beat{beat['beat']:02d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        at = max(beat["start"], beat["end"] - 0.1)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", str(path)],
            check=True,
        )
        frames.append(path)
    return frames


def report(timing: dict, number: int) -> None:
    for beat in timing["beats"]:
        if beat["overrun"] > 0.3:
            print(f"ch{number} beat {beat['beat']}: animations ran {beat['overrun']:.1f} s past the narration")
    print(f"ch{number}: {len(timing['beats'])} beats, {timing['total']:.1f} s")


def join(paths: list[Path], out_path: Path) -> Path:
    """Join the chapters with one re-encode. A stream copy leaves duplicate
    timestamps at chapter seams, because each chapter's length follows its
    voice and is not a whole number of frames; players can stutter there."""
    listing = OUT / "chapters.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path),
        ],
        check=True,
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chapter", type=int, action="append", help="chapter number, repeatable; default all")
    parser.add_argument("--preview", action="store_true", help="no voice, 480p, estimated timing")
    parser.add_argument("--voice", choices=sorted(VOICES), default="kokoro", help="narration engine")
    args = parser.parse_args()
    chosen = [c for c in CHAPTERS if not args.chapter or c["number"] in args.chapter]
    OUT.mkdir(parents=True, exist_ok=True)
    finished = []
    for chapter in chosen:
        number = chapter["number"]
        if args.preview:
            clips = None
            durations = [max(2.5, len(text.split()) / WORDS_PER_SECOND) for text in chapter["beats"]]
        else:
            clips = narrate(chapter["beats"], args.voice)
            durations = [clip.seconds + BREATH_SECONDS for clip in clips]
        durations[-1] += CHAPTER_TAIL_SECONDS
        video, timing = render(chapter, durations, args.preview)
        report(timing, number)
        frames = beat_frames(video, timing, number)
        print(f"ch{number} frames: {frames[0].parent}/ch{number}-beat*.png")
        if args.preview:
            target = OUT / f"ch{number}-preview.mp4"
            shutil.copy(video, target)
        else:
            lengths = [beat["end"] - beat["start"] for beat in timing["beats"]]
            audio = concat_audio(clips, OUT / f"ch{number}-voice.mp3", target_seconds=lengths)
            target = mux_final(video, audio, OUT / f"ch{number}.mp4")
            finished.append(target)
        print(f"ch{number} video: {target}")
    if not args.preview and len(finished) == len(CHAPTERS):
        final = join(finished, OUT / "agentlab-explainer.mp4")
        print(f"final video: {final}")


if __name__ == "__main__":
    main()
