"""Record the video's "Ravi's own assistant" beat against a running local server (live sandbox, replayed AI).

Runs Story B to the open vote, mints Ravi's answer capability, then calls the PUBLIC /mcp endpoint the way any
MCP client would: get_request -> answer_request(yes) -> the same token for Tomas (refused) -> finishes the story
through execute -> get_my_repayment with Ravi's read token. Writes:

- video/remotion/src/data/mcp_transcript.json  (tokens redacted; what the terminal scene types out)
- <scratch>/passbook_links.json                (passbook paths for the phone capture; contains tokens, not in git)

    .venv/Scripts/python -I video/capture/v3/mcp_transcript.py --base http://localhost:8050 --links <scratch file>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _wait(c: httpx.Client, timeout: float = 300) -> dict:
    end = time.monotonic() + timeout
    while True:
        v = c.get("/api/judge/story").json()
        if not v.get("running"):
            return v
        if time.monotonic() > end:
            raise TimeoutError("story step still running")
        time.sleep(1)


def _step(c: httpx.Client, expect: str, **params) -> None:
    r = c.post("/api/judge/story/next", params=params or None)
    r.raise_for_status()
    assert r.json()["step"] == expect, r.text[:200]
    v = _wait(c)
    if v.get("last_error"):
        raise RuntimeError(f"{expect}: {v['last_error']}")


def _redact(obj, tokens: list[str]):
    text = json.dumps(obj, ensure_ascii=False)
    for t in tokens:
        text = text.replace(t, "tkc1.…")
    return json.loads(text)


async def _mcp(base: str, bearer: str, calls: list[tuple[str, dict]]) -> list[dict]:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    out = []
    transport = StreamableHttpTransport(f"{base}/mcp", headers={"Authorization": f"Bearer {bearer}"})
    async with Client(transport) as mcp:
        for name, args in calls:
            res = await mcp.call_tool(name, args, raise_on_error=False)
            out.append({"tool": name, "args": args, "result": res.structured_content})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8050")
    ap.add_argument("--links", required=True, help="where to write the passbook paths (keep out of git)")
    a = ap.parse_args()
    from app.config import get_settings

    bearer = get_settings().mcp_demo_token
    with httpx.Client(base_url=a.base, timeout=300) as c:
        c.get("/api/session").raise_for_status()
        c.post("/api/judge/story/reset", params={"variant": "member_agents"}).raise_for_status()
        _wait(c)
        cid = c.get("/api/session").json()["circle_id"]
        for step in ("advance_to_t3", "run_round3", "reply_as_ravi", "start_recovery"):
            _step(c, step)
        r = c.post(f"/api/circles/{cid}/member-agents/capability", json={"member": "ravi"})
        r.raise_for_status()
        answer = r.json()["token"]
        transcript = asyncio.run(
            _mcp(
                a.base,
                bearer,
                [
                    ("get_request", {"cap": answer, "member": ""}),
                    ("answer_request", {"cap": answer, "choice": "yes", "rationale": "Ravi said yes on WhatsApp: payday is Friday.", "member": ""}),
                    ("answer_request", {"cap": answer, "choice": "yes", "rationale": "", "member": "tomas"}),
                ],
            )
        )
        for step in ("consents", "votes", "approve", "execute"):
            _step(c, step)
        links = c.post(f"/api/passbook/links/{cid}").json()["links"]
        ravi_read = next(link["path"] for link in links if link["member"] == "ravi").split("#", 1)[1]
        transcript += asyncio.run(_mcp(a.base, bearer, [("get_my_repayment", {"cap": ravi_read, "member": ""})]))
    tokens = [answer, *[link["path"].split("#", 1)[1] for link in links]]
    out = REPO / "video/remotion/src/data/mcp_transcript.json"
    out.write_text(
        json.dumps({"label": "recorded transcript", "circle": cid, "calls": _redact(transcript, tokens)}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(a.links).write_text(json.dumps(links, ensure_ascii=False), encoding="utf-8")
    for call in transcript:
        res = call["result"] or {}
        print(call["tool"], call["args"].get("member") or "", "->", res.get("error") or res.get("recorded") or ("ok" if res.get("ok") else res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
