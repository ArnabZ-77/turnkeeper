"""Pull the real ids and DOM snapshots out of the capture logs into remotion/src/data/captured.json.
The composition reads every id, amount and trace line from this file; none is typed into the components."""
import json, re
from pathlib import Path
HERE = Path(__file__).resolve().parent
FOOT = HERE.parent / "public" / "footage"
OUT = HERE.parent / "remotion" / "src" / "data" / "captured.json"
log = json.loads((FOOT / "capture-log.json").read_text(encoding="utf-8"))
desk = json.loads((FOOT / "capture-log.desktop.json").read_text(encoding="utf-8"))
ids = log["ids"]

def snap(scene, name, last=True):
    sc = next(s for s in desk["scenes"] if s["id"] == scene)
    c = [x for x in sc["snapshots"] if x["as"] == name]
    return (c[-1] if last else c[0])["text"]

def receipt(text):
    m = lambda pat: (re.search(pat, text) or [None, None])[1]
    return {
        "title": text.splitlines()[0], "status": text.splitlines()[1], "amount": text.splitlines()[2],
        "recipient": m(r"Recipient\n(.+)"), "item": m(r"Payout item\n(\S+)"), "batch": m(r"Batch\n(\S+)"),
        "paid_at": m(r"Paid at\n(.+)"),
    }

trace = snap("cap-07-round3", "agent-trace")
lines = trace.splitlines()
tools = [l for l in lines if l.startswith("get_order_details →")]
ai_unavail = next(l for l in lines if l.startswith("AI unavailable"))
collect = next(l for l in lines if l.startswith("collect_round({"))
out = {
    "circle": ids["story_circle_id"], "run": ids["story_run_id"], "hash": ids["charter_hash"],
    "invoice": ids["invoice_id"], "payday": log["ids"]["ravi_reply"]["payday"],
    "round3_capture_id": ids["round3_capture_ids_seen_in_trace"][0],
    "ravi_decline": ids["ravi_decline"],
    "trace_round3": {"ai": ai_unavail, "collect": collect, "tools": tools[:5]},
    "receipt3": receipt(snap("cap-12-payout", "receipt")),
    "receipt4": receipt(snap("cap-13-round4", "receipt")),
    "round3_final_status": ids["round3_payout_final_status"],
    "payout3": ids["round3_payout"], "payout4": ids["round4_payout"],
}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote", OUT)
