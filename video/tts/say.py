"""Offline voiceover generator for the Turnkeeper demo video.

Usage:
    tts/.venv/Scripts/python.exe tts/say.py --in script.json --out out_dir

script.json: [{"id": "s01", "text": "...", "voice": "af_heart"?, "lang": "en-us"|"en-gb"|"hi"?, "speed": 1.0?}]

English lines -> Kokoro-82M via kokoro-onnx (CPU, offline; models in tts/models/).
Hindi lines (lang "hi") -> edge-tts hi-IN-SwaraNeural (online) -> mp3 -> 24 kHz mono WAV via ffmpeg.

Writes out_dir/<id>.wav for each line plus out_dir/durations.json ({id: seconds}) and
prints a real-time factor (synthesis time / audio duration) for the Kokoro lines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import soundfile as sf

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
DEFAULT_VOICE = "af_heart"
HINDI_VOICE = "hi-IN-SwaraNeural"
SAMPLE_RATE = 24000

# phonemizer copies espeak-ng.dll to a temp dir and cannot delete it at exit on Windows
# (the DLL is still loaded). Silence that harmless atexit PermissionError noise.
_default_unraisable = sys.unraisablehook


def _quiet_unraisable(u):  # pragma: no cover
    if isinstance(u.exc_value, PermissionError) and "espeak" in str(u.exc_value):
        return
    _default_unraisable(u)


sys.unraisablehook = _quiet_unraisable


MODEL_OVERRIDE: Path | None = None


def find_model() -> Path:
    # int8 measured fastest on this Snapdragon X CPU (native ARM64 Python); fp32 is a fallback.
    if MODEL_OVERRIDE:
        return MODEL_OVERRIDE
    for name in ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx", "kokoro-v1.0.fp16.onnx"):
        p = MODELS / name
        if p.exists():
            return p
    sys.exit(f"No Kokoro model found in {MODELS}. See video/README.md for download links.")


def default_lang(voice: str) -> str:
    # Kokoro voice prefixes: a* = American English, b* = British English
    return "en-gb" if voice.startswith("b") else "en-us"


def hindi_to_wav(text: str, out_wav: Path, voice: str) -> None:
    import edge_tts

    with tempfile.TemporaryDirectory() as td:
        mp3 = Path(td) / "line.mp3"
        asyncio.run(edge_tts.Communicate(text, voice).save(str(mp3)))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_wav)],
            check=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, help="script JSON: [{id, text, voice?, lang?, speed?}]")
    ap.add_argument("--out", dest="out", required=True, help="output directory for WAVs + durations.json")
    ap.add_argument("--model", help="explicit .onnx model path (default: int8, then fp32, in tts/models/)")
    args = ap.parse_args()
    global MODEL_OVERRIDE
    if args.model:
        MODEL_OVERRIDE = Path(args.model).resolve()

    lines = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    kokoro = None
    durations: dict[str, float] = {}
    synth_time = 0.0
    synth_audio = 0.0

    for line in lines:
        lid, text = line["id"], line["text"]
        lang = line.get("lang")
        wav = out / f"{lid}.wav"
        t0 = time.perf_counter()
        if lang == "hi":
            hindi_to_wav(text, wav, line.get("voice") or HINDI_VOICE)
            dur = sf.info(str(wav)).duration
            engine = "edge-tts"
        else:
            if kokoro is None:
                from kokoro_onnx import Kokoro

                tl = time.perf_counter()
                kokoro = Kokoro(str(find_model()), str(MODELS / "voices-v1.0.bin"))
                print(f"Kokoro model {find_model().name} loaded in {time.perf_counter() - tl:.2f}s")
                t0 = time.perf_counter()
            voice = line.get("voice") or DEFAULT_VOICE
            samples, sr = kokoro.create(text, voice=voice, speed=float(line.get("speed", 1.0)),
                                        lang=lang or default_lang(voice))
            sf.write(str(wav), samples, sr, subtype="PCM_16")
            dur = len(samples) / sr
            elapsed = time.perf_counter() - t0
            synth_time += elapsed
            synth_audio += dur
            engine = f"kokoro:{voice}"
        durations[lid] = round(dur, 3)
        print(f"{lid}: {dur:.2f}s audio in {time.perf_counter() - t0:.2f}s [{engine}]")

    (out / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")
    if synth_audio:
        print(f"Kokoro RTF: {synth_time / synth_audio:.3f} ({synth_time:.2f}s compute / {synth_audio:.2f}s audio; "
              f"excludes model load)")


if __name__ == "__main__":
    main()
