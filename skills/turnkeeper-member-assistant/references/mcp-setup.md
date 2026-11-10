# Connecting to Turnkeeper over MCP

Turnkeeper serves a public MCP endpoint at `https://<host>/mcp` (streamable HTTP); the live demo serves it at
`https://turnkeeper-s8xv.onrender.com/mcp`. It needs the operator's bearer token (`MCP_DEMO_TOKEN`), and allows 30
requests a minute per token and IP. Member tools also take the member's own `cap`.

Claude Code:

```bash
claude mcp add --transport http turnkeeper https://turnkeeper-s8xv.onrender.com/mcp \
  --header "Authorization: Bearer $TURNKEEPER_MCP_TOKEN"
```

Any MCP client configuration (JSON):

```json
{
  "mcpServers": {
    "turnkeeper": {
      "type": "http",
      "url": "https://turnkeeper-s8xv.onrender.com/mcp",
      "headers": {"Authorization": "Bearer ${TURNKEEPER_MCP_TOKEN}"}
    }
  }
}
```

Seven tools; `answer_request` is the only one that writes:

| Tool | Needs | Does |
|---|---|---|
| `get_request(cap, member="")` | member token | The proposal the member is asked about |
| `answer_request(cap, choice, rationale="", member="")` | answer token | Records one yes/no; the member's mandate is a floor |
| `get_my_repayment(cap, member="")` | member token | The member's invoice: amount, due date, status |
| `get_circle`, `get_round_matrix`, `get_charter`, `get_payout_status` | bearer only | Read-only, PII-masked demo circle views |

No tool moves money. Without the bearer token, use `scripts/member.py`, which calls the same engine over HTTP
(`/api/member/*`, on the live demo by default) with the member token alone.
