"""Word timings for burned-in captions, aligned to the narration WAVs.

Usage: python video/script/captions.py   (any Python 3.9+ with numpy)
Reads  video/script/voiceover.json (line texts) + video/public/audio/vo/<id>.wav
Writes video/remotion/src/data/captions.json  {line_id: {text, dur, speech: [start, end], words: [{t, s, e}]}}

Method: find the speech span and every internal pause (>= 90 ms below the noise floor) in the WAV. Punctuation
breaks in the text (, . ; : ? !) are snapped to the nearest detected pause, so phrases start when the voice
actually restarts. Inside each phrase, words are spread by length. Times are seconds from the start of the WAV.
"""
from __future__ import annotations
import json, re, wave
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
VO = HERE.parent / "public" / "audio" / "vo"
OUT = HERE.parent / "remotion" / "src" / "data" / "captions.json"
FRAME = 0.010


def read(path: Path):
    with wave.open(str(path)) as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        raw = raw.reshape(-1, ch).mean(axis=1)
    return sr, raw


def pauses(sr, x):
    hop = int(sr * FRAME)
    m = len(x) // hop
    rms = np.sqrt((x[: m * hop].reshape(m, hop) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    peak = np.percentile(db, 95)
    voiced = db > peak - 32
    idx = np.where(voiced)[0]
    if len(idx) == 0:
        return 0.0, len(x) / sr, []
    a, b = idx[0], idx[-1]
    gaps, i = [], a
    while i <= b:
        if not voiced[i]:
            j = i
            while j <= b and not voiced[j]:
                j += 1
            if (j - i) * FRAME >= 0.09:
                gaps.append((i * FRAME, j * FRAME))
            i = j
        else:
            i += 1
    return a * FRAME, (b + 1) * FRAME, gaps


def main():
    lines = json.loads((HERE / "voiceover.json").read_text(encoding="utf-8"))
    out = {}
    for ln in lines:
        lid = ln["id"]
        text = ln.get("caption") or ln["text"]
        sr, x = read(VO / f"{lid}.wav")
        s0, s1, gaps = pauses(sr, x)
        words = text.split()
        w = np.array([len(re.sub(r"\W", "", t)) + 1.5 for t in words], dtype=float)
        cum = np.concatenate([[0], np.cumsum(w)])
        total = cum[-1]
        # break after word i if it ends with punctuation (not the last word)
        brk = [i for i, t in enumerate(words[:-1]) if re.search(r"[,.;:!?।]$", t)]
        used, snap = set(), {}

        def expected(i):
            # interpolate between the nearest already-snapped breaks (or the speech edges) by word length
            lo_i, lo_t = -1, s0
            hi_i, hi_t = len(words) - 1, s1
            for j, (g0, g1) in snap.items():
                if j < i and j > lo_i:
                    lo_i, lo_t = j, g1
                if j > i and j < hi_i:
                    hi_i, hi_t = j, g0
            span = cum[hi_i + 1] - cum[lo_i + 1]
            return lo_t + (hi_t - lo_t) * (cum[i + 1] - cum[lo_i + 1]) / span if span else lo_t

        for strong, tol in ((True, 0.5), (False, 0.22)):
            for i in brk:
                is_strong = bool(re.search(r"[.;:!?।]$", words[i]))
                if is_strong != strong or i in snap:
                    continue
                exp = expected(i)
                best, bd = None, tol
                for k, (g0, g1) in enumerate(gaps):
                    if k in used:
                        continue
                    d = abs((g0 + g1) / 2 - exp)
                    if d < bd:
                        best, bd = k, d
                if best is not None:
                    used.add(best)
                    snap[i] = gaps[best]
        # phrase spans
        starts, ends, cur_s, bounds = [], [], s0, []
        phrases, first = [], 0
        for i in brk + [len(words) - 1]:
            phrases.append((first, i))
            first = i + 1
        res = [None] * len(words)
        t_start = s0
        for pi, (a, b) in enumerate(phrases):
            if b in snap and pi < len(phrases) - 1:
                t_end = snap[b][0]
                next_start = snap[b][1]
            elif pi < len(phrases) - 1:
                t_end = max(expected(b) - 0.02, t_start + 0.1 * (b - a + 1))
                next_start = t_end + 0.05
            else:
                t_end = s1
                next_start = s1
            t_end = max(t_end, t_start + 0.12 * (b - a + 1))
            span = cum[b + 1] - cum[a]
            c = t_start
            for k in range(a, b + 1):
                dur = (t_end - t_start) * (w[k] / span)
                res[k] = {"t": words[k], "s": round(float(c), 3), "e": round(float(c + dur), 3)}
                c += dur
            t_start = next_start
        out[lid] = {"text": text, "dur": round(len(x) / sr, 3), "speech": [round(float(s0), 3), round(float(s1), 3)],
                    "pauses": [[round(float(a), 2), round(float(b), 2)] for a, b in gaps], "words": res}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", OUT, len(out), "lines")


if __name__ == "__main__":
    main()
