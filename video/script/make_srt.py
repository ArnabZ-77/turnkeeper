"""SRT for the film, from script/storyboard.json (line start times) and remotion/src/data/captions.json (word times).
Every spoken line is included (including the ones on slides, which are not burned in).   python video/script/make_srt.py [out.srt]"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sb = json.loads((HERE / "storyboard.json").read_text(encoding="utf-8"))
cap = json.loads((HERE.parent / "remotion/src/data/captions.json").read_text(encoding="utf-8"))
out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "out" / "turnkeeper_v3.srt"
HI = {"H01": "Salary comes on Friday, I'll pay then."}


def ts(t):
    t = max(0.0, t)
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def wrap(words):
    text = " ".join(words)
    if len(text) <= 44:
        return text
    best, bi = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        d = abs(len(a) - len(b))
        if bi is None or d < bi:
            best, bi = (a, b), d
    return best[0] + "\n" + best[1]


cues = []
for sc in sb["scenes"]:
    for ln in sc["lines"]:
        c = cap[ln["id"]]
        words = c["words"]
        i = 0
        page = []
        pages = []
        for w in words:
            page.append(w)
            n = sum(len(x["t"]) + 1 for x in page)
            if (n > 70 and w["t"][-1] in ",;:") or w["t"][-1] in ".!?।" or n > 84:
                pages.append(page)
                page = []
        if page:
            pages.append(page)
        for k, pg in enumerate(pages):
            start = ln["abs"] + pg[0]["s"]
            end = ln["abs"] + (pages[k + 1][0]["s"] if k + 1 < len(pages) else pg[-1]["e"] + 0.25)
            txt = wrap([x["t"] for x in pg])
            if ln["id"] in HI and k == len(pages) - 1:
                txt += "\n[" + HI[ln["id"]] + "]"
            cues.append((start, end, txt))
lines = []
for n, (a, b, t) in enumerate(cues, 1):
    lines += [str(n), f"{ts(a)} --> {ts(b)}", t, ""]
out.write_text("\n".join(lines), encoding="utf-8")
print("wrote", out, len(cues), "cues")
