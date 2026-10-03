"""Render the AgentLab explainer video: narrate, render each chapter, mux, join.

Preview one chapter fast (no voice, durations estimated from word count,
480p, one PNG per beat end for layout checks):

    uv run python scripts/videos/agentlab_explainer/make.py --preview --chapter 3

Final render (Polly generative voice, 1080p30, every chapter, joined):

    AWS_PROFILE=agentlab uv run python scripts/videos/agentlab_explainer/make.py

Outputs land in out/explainer/ at the repo root. Polly clips are cached by
text, so re-rendering a chapter never pays for the same sentence twice.
Manim runs from the uv cache with --offline, so a render never downloads.
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
VOICE = "Matthew"
ENGINE = "generative"
POLLY_REGION = "eu-central-1"
WORDS_PER_SECOND = 2.6
BREATH_SECONDS = 0.35
CHAPTER_TAIL_SECONDS = 0.8
MANIM = ["uvx", "--offline", "--python", "3.12", "manim"]
SCENE_CLASS = "Chapter"


def narrate(texts: list[str]) -> list[NarrationClip]:
    import boto3

    CLIP_CACHE.mkdir(parents=True, exist_ok=True)
    polly = None
    clips = []
    for text in texts:
        said = spoken(text)
        key = hashlib.sha1(f"{VOICE}|{ENGINE}|{said}".encode()).hexdigest()[:16]
        path = CLIP_CACHE / f"{key}.mp3"
        if not path.exists():
            polly = polly or boto3.client("polly", region_name=POLLY_REGION)
            response = polly.synthesize_speech(Text=said, OutputFormat="mp3", VoiceId=VOICE, Engine=ENGINE)
            path.write_bytes(response["AudioStream"].read())
        clips.append(NarrationClip(path=path, seconds=ffprobe_duration(path), text=text))
    return clips


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
    listing = OUT / "chapters.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out_path)],
        check=True,
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chapter", type=int, action="append", help="chapter number, repeatable; default all")
    parser.add_argument("--preview", action="store_true", help="no voice, 480p, estimated timing")
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
            clips = narrate(chapter["beats"])
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
