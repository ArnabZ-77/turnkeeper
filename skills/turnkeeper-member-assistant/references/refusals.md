# Turnkeeper refusals: what they mean and what to say

Over MCP, every refusal is `{"ok": false, "error": "<CODE>", "message": "..."}`. Over HTTP (`/api/member/*`), the
named refusals come back as status 403 with that object as `detail`. Quote the `message`; it is written for people.

Four HTTP errors carry no code:

| Status | What happened | What to do |
|---|---|---|
| 401 `{"detail": "missing X-Turnkeeper-Capability"}` | No token was sent. | Send the member's token in the `X-Turnkeeper-Capability` header (never in a URL). |
| 422 | The answer body failed validation: `choice` is not exactly `yes` or `no` (or `rationale` is over 2,000 characters, or `member` over 40). On `/answer` this is checked before anything else. | Ask the member again for a plain yes or no; never guess. |
| 429 `{"detail": "rate limited"}` | More than 30 requests a minute from this IP. Checked before the token. | Wait the `Retry-After` seconds, then try once more. |
| 500 | Something failed on Turnkeeper's side; `detail` is `{"ok": false, ...}` with a short reason, never a traceback. | Tell the member it failed on the server, not because of them; try once more later. |

Named refusals (403 over HTTP):

| Code | What happened | Tell the member |
|---|---|---|
| `CAPABILITY_SCOPE` | The token was used for another member, a read token tried to answer, the answer was not one of the allowed choices (over MCP; over HTTP that is a 422), or member agents are switched off for this circle. | "This link only lets me answer one question for you, with yes or no." Never retry with another name. |
| `CAPABILITY_USED` | This token already recorded an answer (enforced by the server's database, also across restarts). | "Your answer is already recorded." Read the request again to show it. |
| `CAPABILITY_EXPIRED` | Answer tokens last 15 minutes; read tokens up to a day. | "The link expired; ask your organizer for a new one." |
| `CAPABILITY_INVALID` | Not a Turnkeeper token, or it was changed (signature mismatch). | "That doesn't look like a valid Turnkeeper link." Ask them to paste it again, whole. |
| `SET_SUPERSEDED` | The proposal changed (or was approved, or closed) after the token was issued. | "The proposal changed, so this link no longer applies; ask for a new one." |
| `NOT_A_DEMO_CIRCLE` | Tokens only work on demo circles in this hackathon build. | "This circle isn't open to assistants in the demo." |

A successful answer can still come back with `recorded: "no"` and a `note`: the member's own signed mandate refused
what was asked (for example a swap that moves their payout later than their rule allows). That is the system working
as designed; explain the note, do not argue with it.
