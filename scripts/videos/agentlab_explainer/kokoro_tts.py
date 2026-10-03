"""Synthesize narration clips with Kokoro (open weights, runs locally on ONNX).

make.py runs this in an isolated uv environment, so the project itself never
depends on kokoro-onnx:

    uv run --isolated --no-project --python 3.12 --with kokoro-onnx==0.6.1 \
        python kokoro_tts.py jobs.json

jobs.json holds the model and voice-pack paths, the voice id, the speed, and
a list of {"text", "path"} jobs. Each job becomes a 16-bit mono WAV file.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    kokoro = Kokoro(spec["model"], spec["voices"])
    for job in spec["jobs"]:
        samples, rate = kokoro.create(job["text"], voice=spec["voice"], speed=spec["speed"], lang="en-us")
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(job["path"], "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(pcm.tobytes())
        print(f"wrote {job['path']} ({len(pcm) / rate:.1f} s)")


if __name__ == "__main__":
    main()
