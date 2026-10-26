"""Re-time video/script/storyboard.json from the voiceover durations.

Usage (any Python 3.9+, no dependencies):
    python video/script/retime.py [--durations video/public/audio/vo/durations.json] [--max 172]

Each scene lists `narration_line_ids` and its own `timing` = {lead, gap, tail} in seconds:
    scene.duration = lead + sum(line durations) + gap * (lines - 1) + tail   (or timing.min if longer)
Scenes play back to back (transitions overlap inside lead/tail), so scene.start is the running sum.
Writes `start`, `duration`, `end` and `lines` (absolute and scene-relative start of every voice line) into each
scene, plus `total_sec` at the top, and fails if the total is above --max seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DURATIONS = HERE.parent / "public" / "audio" / "vo" / "durations.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(HERE / "storyboard.json"))
    ap.add_argument("--durations", default=str(DEFAULT_DURATIONS))
    ap.add_argument("--max", type=float, default=172.0, help="hard cap in seconds (2:52)")
    args = ap.parse_args()

    sb_path = Path(args.storyboard)
    sb = json.loads(sb_path.read_text(encoding="utf-8"))
    dur = json.loads(Path(args.durations).read_text(encoding="utf-8"))
    script = {l["id"]: l for l in json.loads((HERE / "voiceover.json").read_text(encoding="utf-8"))}

    t = 0.0
    for sc in sb["scenes"]:
        tm = sc.get("timing", {})
        lead, gap, tail = tm.get("lead", 0.3), tm.get("gap", 0.3), tm.get("tail", 0.45)
        cursor = lead
        lines = []
        for i, lid in enumerate(sc.get("narration_line_ids", [])):
            if lid not in dur:
                sys.exit(f"{sc['id']}: no duration for line {lid}")
            if i:
                cursor += gap
            lines.append({
                "id": lid,
                "audio": f"audio/vo/{lid}.wav",
                "at": round(cursor, 3),
                "abs": round(t + cursor, 3),
                "dur": dur[lid],
                "text": script[lid]["text"],
            })
            cursor += dur[lid]
        length = max(cursor + tail, tm.get("min", 0.0))
        sc["start"] = round(t, 3)
        sc["duration"] = round(length, 3)
        sc["end"] = round(t + length, 3)
        sc["lines"] = lines
        t += length

    sb["total_sec"] = round(t, 3)
    sb["total_mmss"] = f"{int(t // 60)}:{t % 60:05.2f}"
    sb_path.write_text(json.dumps(sb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"total {sb['total_mmss']} ({t:.2f}s) over {len(sb['scenes'])} scenes")
    if t > args.max:
        sys.exit(f"total {t:.2f}s is above the {args.max:.0f}s cap")


if __name__ == "__main__":
    main()
