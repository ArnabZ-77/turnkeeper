# Turnkeeper refusals: what they mean and what to say

Every refusal is `{"ok": false, "error": "<CODE>", "message": "..."}` (HTTP: status 403 with that object as
`detail`). Quote the `message`; it is written for people.

| Code | What happened | Tell the member |
|---|---|---|
| `CAPABILITY_SCOPE` | The token was used for another member, a read token tried to answer, or the answer was not one of the allowed choices. | "This link only lets me answer one question for you, with yes or no." Never retry with another name. |
| `CAPABILITY_USED` | This token already recorded an answer (enforced by the server's database, also across restarts). | "Your answer is already recorded." Read the request again to show it. |
| `CAPABILITY_EXPIRED` | Answer tokens last 15 minutes; read tokens up to a day. | "The link expired; ask your organizer for a new one." |
| `CAPABILITY_INVALID` | Not a Turnkeeper token, or it was changed (signature mismatch). | "That doesn't look like a valid Turnkeeper link." Ask them to paste it again, whole. |
| `SET_SUPERSEDED` | The proposal changed (or was approved, or closed) after the token was issued. | "The proposal changed, so this link no longer applies; ask for a new one." |
| `NOT_A_DEMO_CIRCLE` | Tokens only work on demo circles in this hackathon build. | "This circle isn't open to assistants in the demo." |

A successful answer can still come back with `recorded: "no"` and a `note`: the member's own signed mandate refused
what was asked (for example a swap that moves their payout later than their rule allows). That is the system working
as designed; explain the note, do not argue with it.
