# Security and privacy audit

Date: 2026-11-02. Scope: the `main` branch of this repository, its full git history (13 commits, 3 branches), the
built web bundle, and the behaviour of the running app (probed locally on a temporary database with no PayPal
credentials and no model key). Sandbox only: no real money, no real personal data. This is a self-audit by the
authors, not a third-party penetration test.

Method: read every route, the session, invite, webhook, mandate and policy code, the public MCP and the agent
tool loop; scanned history with literal matches of every value in the local `.env` plus pattern and entropy
scans; started the app on a temporary database and probed it with a second and third "visitor" session.
Nothing here was run against live PayPal or a live model.

## Verdict

- **No secret has ever been committed.** Safe to make the repository public after the three hygiene items in
  "Before the repo goes public".
- **No critical or exploitable-for-money finding.** The money path is gated as documented: sandbox-only config,
  one policy engine in front of every charge and payout, server-computed amounts, deterministic PayPal
  idempotency keys, signed mandate, atomic remedy claim.
- **One high finding (privacy):** free-tier Gemini receives text that visitors type.
- Several medium findings concern availability, session handling and missing browser hardening. All have small fixes.

## Findings, ranked

| # | Severity | Finding | Where |
|---|---|---|---|
| 1 | High (privacy) | Visitor-typed text is sent to a free-tier model that may train on it, with no notice in the app | `app/routes/compile.py:98-104`, `app/routes/recovery.py:164-190`, `app/agent/llm.py` |
| 2 | Medium | The session id is returned in the JSON body and is the only credential for the event stream | `app/routes/circles.py:44`, `app/routes/events.py:39-43` |
| 3 | Medium | `/webhooks/paypal` is unauthenticated, unbounded and spends the shared PayPal budget | `app/routes/webhooks.py:26-34`, `app/engine/webhooks.py:29,189-190` |
| 4 | Medium | Six anonymous cookie-less requests lock every real visitor out for 20 minutes | `app/engine/sessions.py:79-88` |
| 5 | Medium | No browser security headers (CSP, nosniff, frame-ancestors, Referrer-Policy, HSTS) | `app/main.py:51` (no middleware) |
| 6 | Medium | `pay_round` is check-then-act with no lock; concurrent webhooks can double-book the ledger | `app/engine/rounds.py:723-780,880-905`, `app/engine/webhooks.py:185-201` |
| 7 | Medium | Agent money tasks can read member free text and are not pinned to their own circle | `app/agent/treasurer.py:59-79,398-405` |
| 8 | Low | Signing-secret strength is enforced only when `PUBLIC_BASE_URL` is https; `MCP_DEMO_TOKEN` has no length rule | `app/config.py:205-240` |
| 9 | Low | Payout receiver can be set by any holder of the member's link when none is on file | `app/routes/join.py:405-467` |
| 10 | Low | Bearer-style credentials travel in URLs (session id, invite token) and reach access logs | `app/routes/events.py:39`, `app/routes/join.py:302,380` |
| 11 | Low | Invites cannot be revoked before the member vaults; names of all members shown to any invite holder | `app/engine/join.py:139-144`, `app/routes/join.py:108-132` |
| 12 | Low | Exception text is returned to clients and to the public MCP | `app/engine/tasks.py:190`, `app/mcp/internal.py:167-168`, `app/routes/recovery.py:82` |
| 13 | Low | No retention limits; reset and compile spam grow the SQLite file; webhook payloads and `llm_cache` kept forever | `app/engine/sessions.py:160-179`, `app/models.py` |
| 14 | Low | State-changing GETs on the join return and cancel URLs | `app/routes/join.py:302,383` |
| 15 | Low | Front end follows `approve_url` without checking the host | `web/src/pages/JoinPage.tsx:74` |
| 16 | Low | Invoice QR route trusts the stored media type | `app/routes/recovery.py:150-160` |
| 17 | Low | The public MCP uses one shared token and can read any visitor's demo circle by id | `app/mcp/auth.py`, `app/mcp/public.py:72-116` |
| 18 | Low | Repo hygiene: personal commit email, local paths, 950 MB of untracked video footage not ignored, webhook id in a doc | see "Before the repo goes public" |
| 19 | Info | `/healthz` reveals configuration; unknown `/api/*` paths return the HTML shell with 200; container runs as root; dependencies unpinned | `app/main.py:83-101,142-149`, `Dockerfile` |

### 1. Free-tier Gemini receives visitor-typed text (High, privacy)

The README and `.env.example` state "only synthetic demo data reaches the model". That holds for the agent and
forecaster (they see the fixture's synthetic members). It does not hold for text a visitor types:

- `POST /api/circles/compile` sends the pasted rules (up to 4000 characters) to the model in one call.
- `POST /api/judge/reply_as_ravi` accepts free text (up to 1000 characters) that reaches the recovery run as a
  `<member_reply>` block.

The hosted demo reports `llm_configured: true, llm_provider: gemini` on `/healthz`, and the Gemini free tier may
use prompts for training. Nothing in the UI says so (`grep` of `web/src` for privacy, train, third-party finds no
notice), and `llm_cache` stores the prompt text in the database.

Exploit or leak scenario: a judge pastes their own savings circle's rules, with real names and phone numbers,
into the compile page. Those leave the app for Google's free tier.

Fix:
1. Add a visible line on the compile page and next to "Reply as Ravi": "Text you type here may be sent to a
   third-party AI model (Google Gemini free tier) and may be used to improve it. Use made-up names. Nothing real."
2. Mask phone numbers and emails before the model call (reuse `scrub` from `app/engine/story.py:225`).
3. For the hosted demo prefer a provider with a no-training policy, or set `TK_LIVE_LLM_PER_IP_HOUR=0` so typed text
   goes to rules mode and replay only.
4. Never run `scripts/export_llm_cache.py` on a database that has served real visitors (its filter catches emails,
   keys and PayPal ids, not names or free text).

### 2. Session id in the response body; event stream authorised by the id alone (Medium)

`app/routes/circles.py:44` returns `"sid": sess.sid`, the exact value of the HttpOnly `tk_sid` cookie. Any script
on the page, and anything that logs response bodies, can read it, which defeats HttpOnly. `GET
/api/sessions/{sid}/events` (`app/routes/events.py:39-43`) checks only that the id exists, with no cookie.

Verified locally: a client with no cookie, given only the id, received the victim session's `toast`, `chip`,
`forecast` and `trace` events live. Uvicorn's access log records the id because it is in the path.

Exploit scenario: someone with read access to the host's request logs (or a future XSS) takes the id from the SSE
path and watches or replays that visitor's session (cookie replay also gives them the visitor's API access).

Fix:
- Remove `sid` from the `/api/session` body (grep the SPA first; use `circle_id` and `run_id` instead).
- Change the route to `GET /api/events` and read the session from the cookie with `require_session`; drop the id
  from the URL. The SPA's `EventSource` sends cookies on same-origin requests by default.

### 3. Unauthenticated, unbounded webhook intake (Medium)

`POST /webhooks/paypal` reads the whole body (`await request.body()`), then asks PayPal to verify the signature
(`app/engine/webhooks.py:189`) before it checks anything else, then stores up to 64 KB per unique event id
(`PAYLOAD_LIMIT`, line 29), with no rate limit and no body cap.

Verified locally: a 5 MB body was accepted in 0.45 s; 200 forged events with unique ids were stored (database
grew to 9.6 MB). On a deployment with `PAYPAL_WEBHOOK_ID` set, every forged request also spends a
`verify-webhook-signature` call from the shared 40 calls/min FIFO gateway, so a script can delay real charges
and payouts. Forged events are never dispatched on a deployed app (verified: `rejected: unverified`), so there is
no money impact, only denial of service and disk growth.

Also: the event id is stored before verification passes, so a forged event that reuses a real id (visible to
anyone with the debug token) turns PayPal's genuine delivery into a "duplicate" that is dropped.

Fix:
- Reject early: require all five `PAYPAL-*` headers and `Content-Length <= 64 KB` before touching the gateway.
- Add a `RateLimiter` per IP (`app/mcp/auth.py` already has one) in front of the verify call.
- Store the row only after verification (or store unverified events under a separate key such as
  `event_id + ":unverified"`), and cap the table (keep the newest 1000 rows).

### 4. Session-slot exhaustion (Medium, availability)

`create_session` counts sessions seen in the last 20 minutes and refuses new visitors at 6
(`app/engine/sessions.py:79-88`). A new session needs no cookie and no proof of work.

Verified locally: after six cookie-less `GET /api/session` calls, every further call returned 429 (the
waitlist). A script that repeats this every 19 minutes keeps all real visitors on the read-only replay.

Fix: rate-limit session creation per IP (for example 3 per hour using `RateLimiter`), and count idle sessions
differently (a session that never made a second request expires after 2 minutes). Keep the replay fallback.

### 5. No browser security headers (Medium)

No middleware sets any header (`app/main.py`; confirmed on a local run and on the hosted site, which only adds
Cloudflare's). The built bundle loads nothing from third-party hosts (only `react.dev` and `w3.org` as strings),
so a strict policy is feasible. React escapes text; the only HTML sinks are Bryntum tooltips and cells, and
`tooltipHtml` (`web/src/board/mapping.ts:504`) escapes every value, so no XSS was found. Headers are the
defence in depth.

Fix: add one middleware in `app/main.py`:

```python
@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'sha256-<hash of the inline theme script in index.html>'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp
```

Ship the CSP as `Content-Security-Policy-Report-Only` first and load the board and join pages (Bryntum may need
extra `style-src` or `worker-src` entries). `Referrer-Policy: no-referrer` matters because invite tokens sit in
the URL path.

### 6. Payout race: check, then act (Medium, integrity)

`pay_round` checks `live_payout_exists` at the top (`app/engine/rounds.py:741`), then awaits balances and the
PayPal call, and writes the round, ledger and attempt rows only afterwards (lines 880-905). The HTTP routes are
protected by `tasks.running_for`, but the webhook handler is not: when the last two captures' webhooks arrive
together, both handlers can see the round as fully verified and both call `pay_round`
(`app/engine/webhooks.py:185-201`).

PayPal's side is safe: the batch id and `PayPal-Request-Id` are the same deterministic string, so PayPal does not
pay twice. The local side can write two `payout` and `payout_fee` ledger rows and two attempts, or record a
failure after a success. That understates `circle_available`, so later payouts fail closed ("ledger below the
pot"), and the board shows a wrong history. Real webhook delivery is not yet tested (D1 f), so this has not been
seen in practice.

Fix: a per-circle-round `asyncio.Lock` (or an atomic `UPDATE rounds SET payout_status='SENDING' WHERE ... AND
payout_status IS NULL` claim, as `_claim_for_execution` already does for remedies in `app/engine/recovery.py:735`)
around the check and the write, and re-check `live_payout_exists` after acquiring it.

### 7. Agent money tasks: member text in context, circle id not pinned (Medium)

`collect_round` and `payout` tasks hold the money tools (`charge_vaulted_member`, `create_pot_payout`) and also
`get_member_history` (`app/agent/treasurer.py:59-79`), which returns member replies. Replies are framed as data
and angle brackets are neutralised (`app/engine/state.py:289`), but that is a prompt-level defence. The loop
also passes the model's `circle_id` straight to the tool (`treasurer.py:403`) and the internal MCP has no notion
of the task's circle.

What stops the damage today: the policy engine and the signed mandate run inside every tool; amounts are computed
server-side (no money tool takes an amount); recipient order, one payout per round, ledger and balance checks all
apply. An injected instruction could at worst ask for an action the policy would allow anyway, on a circle whose
6-character id the model would have to guess (36^6, about 2.2 billion). So this is defence in depth, not an
open hole.

Fix: (a) remove `get_member_history`, `list_inbox_replies` and `record_note` from the `collect_round` and
`payout` allowlists (they do not need member text); (b) in the loop, overwrite any `circle_id` argument with
`task.circle_id` before `mcp.call_tool`, and reject a mismatch.

### 8. Secret strength checks (Low)

`check_deploy_secrets` refuses default or short secrets only when `PUBLIC_BASE_URL` starts with `https://`
(`app/config.py:205-240`). A TLS-terminated host with the variable left at its localhost default would accept the
public default `dev-only-change-me` (forgeable invite tokens and mandates) and, with no `PAYPAL_WEBHOOK_ID`,
would dispatch unverified webhook events. `MCP_DEMO_TOKEN` has no length rule, and failed MCP auth is limited to
30 guesses a minute per IP.

Fix: also refuse to start when the request-time scheme is https (`x-forwarded-proto`) and the secrets are weak, or
simply require them whenever `PAYPAL_CLIENT_ID` is set; add `MCP_DEMO_TOKEN` (>= 24 characters) to `weak_secrets`.

### 9. Payout receiver can be set by a link holder (Low, conditional)

`POST /api/join/{token}/wallet` lets the invite holder set `payout_receiver` while none is on file
(`wallet_change_allowed`, `app/routes/join.py:405-415`), and for 15 minutes after joining. The demo fixture's
members have `payout_receiver: null` (`app/fixtures/demo_circle.json`), so if the hosted seed provides only plain
vault ids (`TK_SEED_VAULT_IDS` string form), any visitor can name any email for the pot of their own demo circle.
This is sandbox money and the visitor's own session circle, so the impact is low, but the demo then sends
sandbox payouts to unvetted addresses.

Fix: seed every demo member with `payout_receiver` in the object form of `TK_SEED_VAULT_IDS`, and make
`wallet_change_allowed` return false for fixture clones (`circle.kind` is fixture) so only compiled and judge
circles can use it. The mandate binds handles, not receivers (documented in `docs/limitations.md`); binding the
receiver into the signed mandate would close this properly.

### 10 and 11. Credentials in URLs; invites (Low)

- The session id (finding 2) and invite tokens appear in request paths, so they are in access logs
  (uvicorn on Render) and browser history. The invite HMAC is sound (SHA-256 over circle, handle, expiry and a
  per-member nonce, 96-bit tag, `compare_digest`, tamper and handle-swap tested, nonce rotated on vault
  so old links die), but before a member vaults there is no way for the organizer to revoke a link early.
- `GET /api/join/{token}` returns the full names of every member of the circle. Names only; no emails or ids.

Fix: log with a filter that masks `/join/<token>` and `/events` paths; add an organizer "revoke invite" action
that calls `rotate_invite_nonce`; return first names only on the pre-consent card.

### 12 to 17. Smaller items

- 12: `rec.error = f"{type(e).__name__}: {e}"` (`app/engine/tasks.py:190`) goes to `GET /api/tasks/{id}`, and
  `failure_dict` (`app/mcp/internal.py:168`) puts `str(exc)` into public MCP answers. Database or PayPal errors can
  contain SQL, parameters or debug ids. Fix: return `type(e).__name__` plus a short fixed message, log the detail.
  Unhandled exceptions already return a bare 500 (no stack trace).
- 13: nothing expires sessions, circles, inbox messages, charter drafts, `webhook_events.payload` or `llm_cache`;
  `POST /api/session/reset` creates a new circle clone each call and closes (never deletes) the old one. On
  Render the file is in `/tmp` and disappears on restart, which is the only retention control today. Fix: a
  startup and hourly job deleting sessions idle over 24 h with their circles and child rows, and webhook
  payloads over 7 days; rate-limit reset.
- 14: `GET /join/{token}/return` and `/cancel` change state. They need the invite and, for return, PayPal's
  `approval_token_id` that matches the stored setup token, and they do not rely on the session cookie, so there is
  no cookie-based CSRF to exploit. Keep, but do not add other state-changing GETs.
- 15: `window.location.assign(r.approve_url)` trusts the server's PayPal answer. Fix: only navigate when the host
  ends with `.paypal.com`.
- 16: `GET /api/circles/{id}/recovery/qr` serves the stored data URL with its own media type. Fix: allow only
  `image/png` and add `X-Content-Type-Options: nosniff` (finding 5).
- 17: `/mcp` is read-only (verified: four tools, no money tool listed or callable), masks names, emails, phones and
  ids, and answers the same `NOT_A_DEMO_CIRCLE` for missing and non-demo circles. The shared token can read any
  visitor's demo circle by id (36^6 ids, 30 requests a minute), which holds only synthetic data. Fine for the
  demo; do not reuse the pattern with real circles.

## What was checked and is sound

- **Secrets in history:** every non-trivial value in the local `.env` (PayPal client id and secret, webhook id,
  model keys, signing secrets, MCP token, vault and customer ids, payout receivers) was searched literally across
  all commits and the working tree: no hit in code, docs, `docs/live-run`, `eval/*.json`, test fixtures,
  `app/fixtures/replay_canonical.json`, `video/script` or the built bundle. Pattern scans for `gsk_`, `AIza`,
  `sk-ant-`, `sk-`, `ghp_`, private keys, bearer literals and PayPal `A21...` tokens: none. Entropy scan of all
  blobs: only false positives (URLs, a PNG header in a test). All emails in history are `example.com`/`.org`; the
  only phone numbers are `555` fixtures. `.env`, `*.db`, `data/`, `*.pem`, `*.key`, `app/static/`, `web/node_modules`,
  `video/footage`, `video/audio` and `video/out` are ignored; `.env` was never tracked. `.pre-commit-config.yaml`
  runs gitleaks.
- **Authorisation:** every `/api/circles/{id}/...`, `/api/judge/*`, `/api/tasks/{id}` route checks that the circle
  belongs to the cookie's session. Tested with two sessions: reads (board, invites, charter, recovery, mandates)
  and writes (collect, advance clock, member-agents run) on another session's circle all returned 403; task ids of
  another session read as unknown (404); no cookie gives 401. 403 comes before 404, so circle ids cannot be probed.
- **Cookie:** `HttpOnly; SameSite=Lax; Path=/`, `Secure` on https or `X-Forwarded-Proto: https` (verified). A
  cookie value never creates a session; ids are `token_urlsafe(24)`. CSRF: no CORS is configured, JSON bodies need
  a JSON content type, and Lax cookies are not sent on cross-site POSTs.
- **Static serving:** the SPA fallback resolves the real path and requires it to stay inside `app/static`
  (`app/main.py:140-149`); encoded `..`, double slashes and `.env` all returned the HTML shell (tested).
- **Redirects:** join redirects are relative and the token must match `^[a-z0-9._]{1,80}$`; no open redirect.
- **Injection:** all database access is SQLModel/SQLAlchemy ORM; the only raw statements are fixed `PRAGMA` and
  schema `ALTER TABLE` strings built from model metadata, plus one atomic `UPDATE` through SQLAlchemy.
- **XSS:** no `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `document.write` in `web/src`; Bryntum cells use
  `DomConfig` text, event names go through `encodeHtml`, tooltips through `escapeHtml`.
- **Webhooks:** on a deployed app an event without a valid PayPal signature is stored but never acted on
  (tested); duplicates by event id are dropped; PayPal checks `cert_url`, so there is no server-side fetch (no SSRF).
- **Debug endpoint:** `/api/debug/webhooks` needs the bearer token on https (tested: 401 without, 200 with);
  the hosted site answers 401 without a token.
- **Sandbox only:** `PAYPAL_ENV` other than `sandbox` fails validation (`app/config.py:102-107`); the only base
  URL is a constant; the toolkit adapter forces `Context(sandbox=True)` and compares its base URL
  (`app/paypal/toolkit_adapter.py:146-155`). There is no setting that reaches live PayPal.
- **Payments:** no route or MCP tool takes an amount from the client except the policy-only `try_overcharge`
  (it never calls PayPal). The policy engine checks frozen state, charter hash, exact charter amount, retries,
  recipient, mandate or organizer approval, collection complete, ledger and balance, one payout per round
  (`app/domain/policy.py`). Idempotency: `PayPal-Request-Id` per run, circle, round, member and attempt; payout
  batch id doubles as the key. Mandate: HMAC-SHA256 over canonical JSON of every field but the signature,
  `compare_digest`, checks charter hash, recipient for the round, cap and expiry; secrets under 16 characters or
  the default are refused on https. Remedy execution is claimed atomically.
- **LLM and agents:** model output only proposes; `execute_remedies` and `reschedule_turns` are never in any agent
  allowlist; the recover task holds no money tool; tool calls capped at 25; unknown tool names are refused;
  member replies and charter rules text are fenced (`<member_reply>`, `<rules_text>`) with angle brackets
  neutralised; member agents decide by a deterministic table and the model only words the sentence.
- **Replay recording:** `/api/replay` serves a recording scrubbed of emails and phones; any run in which an
  Advanced Judge control was used is never served; the shipped canonical file contains only `REPLAY-` ids.
- **Dependencies:** `npm audit` reports 0 vulnerabilities. Python dependencies were not audited (no `pip-audit` in
  the environment); run it before release.

## Privacy and data

Personal data the app can hold:

| Data | Where | Who sees it |
|---|---|---|
| Member display names (synthetic in the demo; typed by the organizer in a compiled circle) | `members`, charter JSON, board | the session; first names only on `/mcp` |
| Payer PayPal email returned by Vault, used as default payout receiver | `members.payout_receiver` | stored plain in SQLite; shown masked (`a***@domain`) |
| Vault and customer ids | `members` | server only; never in any response (checked in the board model, join card, MCP) |
| Member replies (English and Hindi), outbound nudges | `inbox_messages`, `llm_cache`, agent traces | the owning session; replies reach the model |
| Typed charter rules | `charter_drafts`, `llm_cache` | the owning session; sent to the model (finding 1) |
| Visitor IP | in memory only, for rate limits; uvicorn access log | host logs |
| Webhook bodies | `webhook_events.payload` (64 KB each) | debug endpoint (token) |

The database is a SQLite file (`TK_DB_PATH`; `/tmp/turnkeeper.db` on Render, `./data/turnkeeper.db` locally,
git-ignored). It is not encrypted and has no retention job (finding 13).

Public repository check: all members are synthetic (Asha, Ravi, Meena, Tomas, Lin, Dev; "Priya" is a composite
persona); all addresses are `example.com`; `docs/interview-script.md` is a script and `docs/sources.md` records
no interview data; no real person's data appears in code, docs, tests, eval files, the replay fixture or the
sample screenshots. Two things do identify the author and are worth a deliberate decision: the git author email on
all 13 commits, and the Windows user name in a few paths.

### Proposed privacy statement (README and the compile page)

> **Privacy.** Turnkeeper is a hackathon demo that runs on the PayPal sandbox. No real money moves and no real
> person's data is needed. Please use made-up names, emails and phone numbers.
> The demo keeps what you type (charter rules, replies, member names) in a temporary database for your session
> and does not sell or share it. On the hosted demo the AI features may send the rules text or reply you type to
> a third-party model provider (Google Gemini free tier), which may use it to improve its models; with no AI key,
> or when the hourly AI limit is reached, the app uses built-in rules and sends nothing. The hosted database is
> erased when the server restarts. PayPal sign-in happens on PayPal's own sandbox page; Turnkeeper never sees your
> password. To delete your data earlier, press "Reset demo" or email the maintainer.

(Make "erased when the server restarts" true only while the Render free plan keeps `/tmp`; change the sentence
if a disk is added.)

## Before the repo goes public

1. Add `video/footage-src/` (950 MB, untracked, not ignored) to `video/.gitignore`; check `git status` shows no
   `??` entry over a few KB before any `git add -A`. The video footage in `video/footage` is already ignored.
2. Decide about the author identity: all commits carry a personal Gmail address. Use a GitHub no-reply address
   going forward (`git config user.email <id>+<name>@users.noreply.github.com`); rewriting history is optional
   and only worth it before the first public push.
3. Remove local paths: `docs/live-run/2026-10-25-rerun.md:11`, the temp path in
   `docs/live-run/raw-2026-10-25/server.log:8`, `docs/innovation-plan.md:153-158` and the example in
   `scripts/record_replay.py:11` (the Windows user name appears in the first two).
4. `docs/decisions.md:15` contains the sandbox webhook id. It is an identifier, not a secret (PayPal signs the
   events; the id alone cannot forge one), but replace it with `<webhook id>` to keep the sandbox account out of
   search results.
5. Add a `.dockerignore` (`.env*`, `.git`, `data`, `video`, `web/node_modules`, `.venv`) so a local
   `docker build` can never copy secrets, and a non-root `USER` in the `Dockerfile`.
6. Add `gitleaks` and `pip-audit` / `npm audit` to `.github/workflows/ci.yml` (gitleaks only runs locally via
   pre-commit today). Pin Python dependencies with a lock file for the image.
7. Rotate the model keys and the MCP token if they were ever pasted into a shared chat, an issue, a screenshot or
   a screen share. None is in the repository; treat anything shown on screen as exposed.
