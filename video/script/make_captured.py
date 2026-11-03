"""Pull the real ids and DOM snapshots out of the v2 capture logs (+ the PayPal read-back) into
remotion/src/data/captured.json. The composition reads every id, amount and status from this file; none is typed
into a component.   python video/script/make_captured.py"""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOOT = HERE.parent / "public" / "footage" / "v2"
DATA = HERE.parent / "remotion" / "src" / "data"
logs = [
    json.loads((FOOT / f).read_text(encoding="utf-8"))
    for f in ("capture-log.v2.json", "capture-log.v2b.json")
]
scenes = {s["id"]: s for lg in logs for s in lg["scenes"]}
raw = "".join((FOOT / f).read_text(encoding="utf-8") for f in ("capture-log.v2.json", "capture-log.v2b.json"))


def snap(scene, name):
    c = [x for x in scenes[scene]["snapshots"] if x["as"] == name]
    return c[-1]["text"]


def receipt(text):
    def m(pat):
        return (re.search(pat, text) or [None, None])[1]

    ls = text.splitlines()
    return {
        "title": ls[0],
        "status": ls[1],
        "amount": ls[2],
        "recipient": m(r"Recipient\n(.+)"),
        "item": m(r"Payout item\n(\S+)"),
        "batch": m(r"Batch\n(\S+)"),
        "paid_at": m(r"Paid at\n(.+)"),
    }


story_bar = snap("v2-00-reset", "story-bar")
circle = re.search(r"New circle (\w+), run (\w+)", story_bar)
ledger = snap("v2-12-payout", "charter-ledger")
refusal = snap("v2-13-refusal", "overcharge").splitlines()
out = {
    "circle": circle.group(1) if circle else None,
    "run": circle.group(2) if circle else None,
    "hash": re.search(r"Charter hash\n(\w+)", ledger).group(1),
    "invoice": sorted(set(re.findall(r"INV2-[A-Z0-9-]+", raw)), key=len)[-1],
    "payday": "2027-01-08",
    "receipt3": receipt(snap("v2-12-payout", "receipt")),
    "receipt3_later": receipt(snap("v2-12b-success", "receipt-after")),
    "receipt4": receipt(snap("v2-14-round4", "receipt")),
    "refusal": {"headline": refusal[0], "reason": refusal[1], "chip": refusal[2]},
    "mandate_labels": [
        ln for ln in snap("v2-00-reset", "mandates").splitlines() if ln.startswith(("Mandate:", "Signature:"))
    ],
}
(DATA / "captured.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
