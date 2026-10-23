# Module contracts (M1: thinnest end-to-end slice)

This is the interface spec that parallel builders code against. If you must deviate, change this file in the
same commit and say why. Keep names exactly as written: other modules import them.

Ground rules for every module:
- Python 3.11, async-first (`httpx.AsyncClient`), type hints everywhere, pydantic v2 models.
- Money is `decimal.Decimal` with 2 places internally and a `"200.00"` string on the wire/DB. Never float.
- **Sandbox only.** `app.config.get_settings().paypal_base_url` is the only base URL. Never read env vars directly;
  use `get_settings()`.
- No secrets in code, logs or test fixtures. Tests mock PayPal with `respx` and never call the network.
- Every PayPal POST carries a `PayPal-Request-Id` built by `app.paypal.keys`.
- Logical handles in anything the LLM sees: member `handle` (e.g. `"ravi"`), `round_n` (int), never raw PayPal IDs
  as inputs. Outputs may include real IDs for display.
- Logging via `logging.getLogger("turnkeeper.<module>")`. Never log tokens, secrets or full webhook bodies.

Repo layout (owner in brackets):

```
app/
  config.py                 [done]  get_settings() -> Settings
  main.py                   [done]  FastAPI app; auto-includes app.routes.{circles,judge,join,webhooks,events}.router
  db.py  models.py          [B4]    SQLModel engine/session + tables
  fixtures/demo_circle.json [B4]    the 6-member demo circle
  paypal/                   [B1]    client, gateway, keys, vault, orders, payouts, balances, refunds, webhooks, toolkit_adapter
  domain/                   [B2]    charter, billing_plan, fees, ledger, mandate, policy
  mcp/                      [B3]    internal (full tools), public (read-only), auth
  agent/                    [B3]    treasurer loop, handles, prompts/, rules_mode
  engine/                   [B4]    events (SSE bus), sessions, clock, rounds, webhooks (reconciler)
  routes/                   [B4]    circles, judge, join, webhooks, events
scripts/smoke/              [B1]    a_setup_token.py ... g_toolkit_wrap.py ; scripts/register_webhook.py
web/                        [B5]    Vite + React + TS + Bryntum Scheduler (trial) -> builds to app/static
tests/                      [each builder owns tests for its modules]
```

---

## B1 · `app/paypal/*` (REST client, gateway, typed wrappers, toolkit adapter, smoke scripts)

### `app/paypal/client.py`

```python
class PayPalError(Exception):
    status: int; name: str; message: str; debug_id: str | None; details: list[dict]; raw: dict
    # name is PayPal's error "name" (e.g. "UNPROCESSABLE_ENTITY") and details[].issue holds codes like
    # "INSTRUMENT_DECLINED". Helper: .issue_codes -> list[str]

class PayPalResponse(BaseModel):
    status: int; data: dict; headers: dict[str, str]

class PayPalClient:
    def __init__(self, settings: Settings | None = None, http: httpx.AsyncClient | None = None): ...
    async def get_token(self, force: bool = False) -> str
        # POST /v1/oauth2/token (client_credentials, basic auth). Cache in memory; refresh when < 300 s left
        # or on force. Thread/async safe (asyncio.Lock).
    async def request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None,
                      headers: dict | None = None, request_id: str | None = None) -> PayPalResponse
        # Adds Authorization, Content-Type, and PayPal-Request-Id when given (POST/PUT/PATCH).
        # One automatic retry with a fresh token on 401. Retries 429 and 5xx with backoff 1/2/4 s (max 3),
        # NEVER retries other 4xx (business errors). Raises PayPalError on any final non-2xx.
        # 204 -> data = {}.
    async def aclose(self) -> None
```

### `app/paypal/gateway.py`

```python
class Gateway:
    """Single FIFO worker + token bucket in front of PayPalClient. Shared by raw calls and the toolkit adapter."""
    def __init__(self, client: PayPalClient, rate_per_min: int = 40, record: Callable[[dict], None] | None = None): ...
    async def call(self, method, path, *, json=None, params=None, headers=None, request_id=None) -> PayPalResponse
        # Enqueue; the worker runs calls strictly in order, spaced by the bucket (default 40/min, 300 ms min gap).
        # `record` is called with {"ts", "method", "path", "status", "request_id", "ms"} for every call (B4 stores it).
    def position(self) -> int                      # queued jobs ahead (for the UI "queued, position N" badge)
    async def run_sync(self, fn: Callable[[], T]) -> T   # run a blocking fn (toolkit) through the same queue+bucket, in a thread
    async def start(self) / async def stop(self)

def get_gateway() -> Gateway   # process-wide singleton (lazy), used by engine/mcp
```

### `app/paypal/keys.py`

```python
def order_request_id(run_id, circle_id, round_n, member, attempt) -> str      # "tk:{run}:{c}:{r}:{m}:a{n}"
def setup_request_id(run_id, circle_id, member) -> str                        # "tk:{run}:{c}:setup:{m}"
def payout_batch_id(run_id, circle_id, round_n, rev) -> str                   # "tk:{run}:{c}:{r}:r{rev}"
def payout_item_id(run_id, circle_id, round_n, member) -> str                 # "{run}{c}{r}{m}" (<= 63 chars)
def invoice_number(run_id, circle_id, round_n, member) -> str                 # "TK-{run}-{c}-{r}-{m}"
def new_run_id() -> str                                                       # 8-char lowercase base32
# All builders assert the documented max lengths (Request-Id <= 108, sender_batch_id <= 30, sender_item_id <= 63).
```

### Typed wrappers (each is `async def ...(gw: Gateway, ...) -> dict` returning `response.data`)

```python
# vault.py
create_setup_token(gw, *, request_id, description, billing_plan: dict | None, return_url, cancel_url,
                   usage_pattern="RECURRING_PREPAID") -> dict      # POST /v3/vault/setup-tokens ; returns data incl. links
approve_link(setup_token_data) -> str | None                        # rel == "approve" or "payer-action"
create_payment_token(gw, *, setup_token_id, request_id) -> dict     # POST /v3/vault/payment-tokens -> {"id": vault_id, "customer": {"id"}}
list_payment_tokens(gw, *, customer_id) -> dict                     # GET /v3/vault/payment-tokens?customer_id=
delete_payment_token(gw, *, token_id) -> None

# orders.py
charge_vaulted(gw, *, request_id, vault_id, amount: Decimal, currency="USD", reference_id, custom_id, description,
               mock_decline: bool = False) -> dict
    # POST /v2/checkout/orders  intent=CAPTURE, payment_source.paypal.vault_id. When mock_decline is True add header
    # PayPal-Mock-Response: {"mock_application_codes":"INSTRUMENT_DECLINED"}. Returns the order dict (status COMPLETED
    # with purchase_units[0].payments.captures[0] on success). Raises PayPalError on decline.
get_order(gw, *, order_id) -> dict
capture_summary(order: dict) -> CaptureSummary   # pydantic: order_id, capture_id, status, gross, fee, net (Decimal; from
                                                  # seller_receivable_breakdown; fee/net None when absent)
create_vault_with_purchase_order(gw, *, request_id, amount, return_url, cancel_url, ...) -> dict   # F18 fallback join
capture_order(gw, *, order_id, request_id) -> dict

# payouts.py
create_payout(gw, *, sender_batch_id, items: list[PayoutItem], email_subject) -> dict     # POST /v1/payments/payouts
PayoutItem(BaseModel): recipient_type: "EMAIL"|"PHONE"; receiver: str; wallet: "PAYPAL"|"VENMO"; amount: Decimal;
                       currency="USD"; note: str; sender_item_id: str
get_payout_batch(gw, *, batch_id) -> dict
get_payout_item(gw, *, item_id) -> dict
cancel_payout_item(gw, *, item_id) -> dict
first_item(batch: dict) -> dict | None     # items[0]
item_status(item: dict) -> str             # "SUCCESS" | "UNCLAIMED" | "PENDING" | "FAILED" | ...

# balances.py
get_balances(gw, *, currency="USD") -> dict                       # GET /v1/reporting/balances?currency_code=USD
available_amount(balances: dict, currency="USD") -> Decimal | None

# refunds.py
refund_capture(gw, *, capture_id, request_id, amount: Decimal | None = None, note="") -> dict

# webhooks.py
register_webhook(gw, *, url, event_types: list[str]) -> dict       # POST /v1/notifications/webhooks
verify_signature(gw, *, headers: Mapping[str, str], body: bytes, webhook_id: str) -> bool
    # POST /v1/notifications/verify-webhook-signature using PAYPAL-AUTH-ALGO, PAYPAL-CERT-URL, PAYPAL-TRANSMISSION-ID,
    # PAYPAL-TRANSMISSION-SIG, PAYPAL-TRANSMISSION-TIME and the raw body parsed as JSON for webhook_event.
WEBHOOK_EVENTS = ["VAULT.PAYMENT-TOKEN.CREATED", "VAULT.PAYMENT-TOKEN.DELETED", "PAYMENT.CAPTURE.COMPLETED",
                  "PAYMENT.CAPTURE.DENIED", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
                  "PAYMENT.PAYOUTS-ITEM.FAILED", "PAYMENT.PAYOUTSBATCH.SUCCESS", "INVOICING.INVOICE.PAID",
                  "CUSTOMER.DISPUTE.CREATED"]
```

### `app/paypal/toolkit_adapter.py`

```python
class ToolkitAdapter:
    """Runs PayPal Agent Toolkit tools (paypal_agent_toolkit.shared) through our token cache, idempotency and gateway."""
    def __init__(self, gw: Gateway, client: PayPalClient, actions: dict | None = None): ...
        # Build Configuration(actions=..., context=Context(sandbox=True)) and PayPalAPI(client_id, secret, context).
        # Replace api._paypal_client with a subclass of paypal_agent_toolkit.shared.paypal_client.PayPalClient whose
        # get_access_token() returns our cached token (sync bridge: cache the last token string; refresh via
        # asyncio.run_coroutine_threadsafe is NOT allowed — instead pre-warm: `await adapter.prepare()` before use)
        # and whose post() adds "PayPal-Request-Id" from a contextvar set by run().
    async def prepare(self) -> None                       # refresh the cached token string for the sync client
    async def run(self, method: str, params: dict, *, request_id: str | None = None) -> dict
        # Executes api.run(method, params) via gw.run_sync. The toolkit returns a JSON string -> parse to dict.
    def tool_names(self) -> list[str]
DEFAULT_ACTIONS = {"orders": {"get": True}, "invoices": {"create": True, "send": True, "generate_qr_code": True,
                   "send_reminder": True}, "disputes": {"list": True}, "transactions": {"list": True}}
```

### `scripts/smoke/*.py` (B1) and `scripts/register_webhook.py`

Standalone, `python -I`-safe, read `.env` via `app.config`, print one JSON result per line and a final `PASS`/`FAIL`
line. Each one is named in `docs/d1-smoke.md`. `a_setup_token.py` prints the approve link and waits for Enter before
creating the payment token. `b_mit_capture.py --vault-id ... --amount 1.00`. `c_mock_decline.py --vault-id ...`.
`d_payouts.py --receiver email [--venmo +1555...] [--unclaimed]`. `e_balances.py`. `f_webhook.py --public-url ...`
(registers, prints the webhook id, polls the app's `/api/debug/webhooks` for 60 s). `g_toolkit_wrap.py --order-id ...`.

### Tests (B1): `tests/paypal/test_client.py`, `test_gateway.py`, `test_keys.py`, `test_wrappers.py` with `respx`.
Cover: token caching + 401 refresh, 429 retry, 4xx no-retry + PayPalError.issue_codes, Request-Id header presence,
FIFO ordering and bucket spacing (use a tiny rate and `asyncio` timing), key lengths, capture_summary parsing,
mock-decline header, verify_signature payload shape, toolkit adapter token injection (monkeypatch `requests`).

**B1 implementation notes (2026-10-23, additive):**
- `PayPalError.message` is never empty: PayPal `message` -> OAuth `error_description` -> a non-JSON body (`raw["_text"]`)
  -> the HTTP reason phrase (`client.http_reason(404) == "Not Found"`, or `"HTTP <status>"`).
- Smoke scripts share `scripts/smoke/_common.py` (`emit()` JSON lines with token-like keys redacted, `run()` ->
  PASS/FAIL + exit 0/1, 2 = no sandbox credentials, 130 = interrupted; `SmokeGateway` = client + gateway + a `calls`
  summary line). `scripts/` and `scripts/smoke/` are packages; each script inserts the repo root into `sys.path` and
  `_common` chdirs to the repo root so `.env` is found under `python -I` from any cwd.
- Extra flags beyond the contract: `d_payouts.py --unclaimed-receiver <email> --poll <s> --skip-dedupe` (dedupe
  PASSes on "same batch id returned" or a 4xx rejection); `f_webhook.py --vault-id` (triggers a $1.00 capture so a
  real PAYMENT.CAPTURE.COMPLETED arrives) and `--allow-unverified`; `a_setup_token.py --no-billing-plan --list`;
  `register_webhook.py --list | --delete <id>` (never edits `.env`). `tests/paypal/test_smoke_scripts.py` runs
  b/c/e against respx.

---

## B2 · `app/domain/*` (pure logic, no I/O)

### `app/domain/charter.py`

```python
RemedyType = Literal["retry_on_date", "turn_swap", "buffer_advance", "repay_by_invoice"]
FeePolicy = Literal["net_pot", "gross_up", "organizer_absorbs"]

class Member(BaseModel):
    handle: str            # ^[a-z][a-z0-9_]{1,15}$ ; unique in a charter
    name: str
    lang: str = "en"
    email: str | None = None
    payday_rule: str | None = None          # free text, e.g. "every 2nd Friday"
    payout_wallet: Literal["PAYPAL", "VENMO"] = "PAYPAL"
    payout_receiver: str | None = None      # email (PAYPAL) or E.164 phone (VENMO)
    track_record_rounds: int = 0            # completed rounds in earlier circles; 0 = no track record

class Buffer(BaseModel):
    seed: Decimal = Decimal("0.00"); source: Literal["organizer"] = "organizer"; retention_pct: int = 0

class Charter(BaseModel):
    name: str; currency: Literal["USD"] = "USD"; amount: Decimal; cadence: Literal["monthly", "weekly"] = "monthly"
    start_date: date; members: list[Member]; rotation: list[str]        # member handles, payout order
    fee_policy: FeePolicy = "net_pot"; buffer: Buffer = Buffer(); grace_days: int = 3
    quorum: str = "4/6"                      # "k/n" ; validator: n == len(members), 1 <= k <= n
    remedy_menu: list[RemedyType] = [...all four...]; languages: list[str] = ["en"]
    # validators: amount > 0 with 2 places; rotation is a permutation of member handles;
    # members with track_record_rounds == 0 must not precede any member with track_record_rounds > 0  (collateral rule)

def canonical_json(charter: Charter) -> str      # sorted keys, Decimal -> str, dates ISO, no whitespace
def charter_sha256(charter: Charter) -> str
def quorum_tuple(charter) -> tuple[int, int]
def recipient_for_round(charter, round_n: int) -> str          # 1-based
def rounds_total(charter) -> int                                 # == len(members)
def due_date(charter, round_n) -> date
```

### `app/domain/billing_plan.py`
`billing_plan_from_charter(charter) -> dict` producing the Vault v3 `billing_plan` shape in PLAN.md §4 (name,
billing_cycles[REGULAR, total_cycles=len(members), FIXED price, frequency MONTH/WEEK, start_date ISO]).

### `app/domain/fees.py`
```python
def money(x) -> Decimal                           # quantize to 0.01, ROUND_HALF_UP
def estimate_fee(gross: Decimal, pct=Decimal("0.0349"), fixed=Decimal("0.49")) -> Decimal
def net_of(gross, fee) -> Decimal
def pot_amount(charter, net_contributions: list[Decimal], advance_net: Decimal, payout_fee_reserve: Decimal) -> Decimal
    # net_pot: Σ net + advance_net - payout_fee_reserve; gross_up / organizer_absorbs: charter.amount * len(nets) + advance_net
    # (fees borne by the grossed-up charges or the organizer). Never negative.
def wire_amount(x) -> str                        # "200.00" (B2 addition; use it for every amount on the wire)
```

### `app/domain/mandate.py`
```python
class Mandate(BaseModel): charter_sha256: str; recipients: list[str]; cap_gross_per_round: Decimal; expires: date;
                          approved_at: datetime; approved_by: str; user_agent: str = ""; signature: str = ""
def sign_mandate(m: Mandate, secret: str) -> Mandate        # HMAC-SHA256 over canonical fields, hex
def verify_mandate(m: Mandate, secret: str, *, charter_sha256: str, round_n: int, recipient: str, amount: Decimal,
                   today: date) -> tuple[bool, str]           # (ok, reason)
```

### `app/domain/ledger.py` (pure functions over a `LedgerView` snapshot; persistence is B4's)
```python
class LedgerEntry(BaseModel): kind: Literal["contribution_net","buffer_seed","buffer_advance","advance_repaid",
                              "payout","payout_fee","refund","capture_refund"]; amount: Decimal  # >= 0; the kind carries the sign
                              round_n: int | None = None; member: str | None = None; ref: str | None = None
class LedgerView(BaseModel): entries: list[LedgerEntry] = []
def circle_available(view) -> Decimal       # Σ contribution_net + buffer_advance + refund - payout - payout_fee - capture_refund
def buffer_balance(view) -> Decimal         # Σ buffer_seed + advance_repaid - buffer_advance
def round_net_collected(view, round_n) -> Decimal   # Σ contribution_net for that round (advances excluded)
```
B2 deviation (2026-10-22): the first draft had `circle_available = … + advance_repaid + refund(+) …` with no
`buffer_advance` term. That counted a repayment in both balances and left the advanced share out of the circle, so
the demo's advance-then-payout path would fail the `circle_available` gate. Now an advance moves money buffer -> circle
and a repayment restores the buffer only. What B4 records: `buffer_advance` when a remedy covers a share,
`advance_repaid` when the invoice is paid (net), `refund` when a cancelled UNCLAIMED payout item returns to the circle
(so the `r{rev+1}` payout passes the gate), `capture_refund` (new kind) when a contribution is refunded to a member
(dissolve, void, canary).

### `app/domain/policy.py`
```python
Action = Literal["charge", "payout", "buffer_advance", "turn_swap", "refund", "cancel_unclaimed", "retry_charge"]
class CircleState(BaseModel):
    circle_id: str; run_id: str; round_n: int; charter_sha256: str; frozen: bool = False
    contributions: dict[str, Literal["PENDING","COMPLETED","DECLINED","COVERED"]]   # member handle -> status for round_n
    attempts: dict[str, int] = {}                 # member -> charge attempts this round
    buffer_balance: Decimal; circle_available: Decimal; paypal_available: Decimal | None
    open_advances: dict[str, Decimal] = {}; swaps_done: set[str] = set(); live_payout_exists: bool = False
    mandate_ok: bool = False
    # Additive and optional (B2, 2026-10-22); the engine fills them in when it knows them:
    rotation_override: list[str] | None = None   # effective payout order after approved swaps. Without it, a recipient
                                                 # other than charter.rotation[round_n-1] is accepted only when both parties
                                                 # are in swaps_done.
    last_attempt_at: dict[str, datetime] = {}; now: datetime | None = None   # enables the 24-virtual-hour retry spacing
    decline_codes: dict[str, str] = {}           # last decline issue code per member; PAYER_ACTION_REQUIRED is never retried
    revoked: set[str] = set()                    # members whose vault token was deleted (VAULT.PAYMENT-TOKEN.DELETED): no charges
class PolicyTicket(BaseModel): id: str; action: Action; circle_id: str; round_n: int; member: str | None; amount: Decimal | None; issued_at: datetime
class PolicyDenied(Exception): reason: str       # the string the UI shows; str(e) == e.reason
class PolicyEngine:
    def __init__(self, charter: Charter, state: CircleState): ...
    def authorize(self, action: Action, *, member: str | None = None, amount: Decimal | None = None,
                  recipient: str | None = None, swap_with: str | None = None, consents: set[str] = frozenset()) -> PolicyTicket
    # Rules (PLAN.md §5): charge exactly charter.amount, one success per member/round; retries <= 2 per member/round;
    # payout recipient == rotation[round_n-1] (or approved swap), amount <= min(cap, circle_available), paypal_available
    # >= amount when known, exactly one live payout; buffer_advance <= buffer_balance and <= one net contribution and
    # at most one open per member; swaps need both consents and at most one per member; frozen -> deny everything.
    # Details fixed by B2: state.charter_sha256 must equal charter_sha256(charter) (else denied). `charge` and
    # `retry_charge` share the attempt limit (attempts[member] <= 2 before this one); retry_charge also needs status
    # DECLINED. Payout needs mandate_ok or "organizer" in consents, every member COMPLETED|COVERED, cap =
    # charter.amount * len(members) (PolicyEngine.gross_pot_cap). "One net contribution" =
    # fees.net_of(amount, estimate_fee(amount)) (PolicyEngine.one_net_contribution; size advances with it). buffer_advance
    # and turn_swap must be in charter.remedy_menu; swaps only touch unpaid turns and must keep the collateral rule.
    # refund needs "organizer" in consents, amount <= charter.amount and a COMPLETED contribution; cancel_unclaimed needs
    # live_payout_exists. Not enforceable here (engine-level): tool-call and run-rate limits, vote closing, injection rules.
```
Tests (B2): `tests/domain/test_*.py`, 30+ cases including the collateral rule, hash stability (key order, whitespace),
HMAC tamper detection, every policy rule's allow and deny path.

---

## B3 · `app/mcp/*` and `app/agent/*`

### `app/mcp/internal.py`
`paypal_ops_internal: FastMCP` (name "paypal-ops"). Never mounted over HTTP. Tools are async functions registered with
`@paypal_ops_internal.tool`. They receive logical handles and call B4's engine functions (import from `app.engine.rounds`
and `app.engine.state`; see B4 contract). Tool list for M1:

| tool | input | returns |
|---|---|---|
| `get_circle(circle_id)` | | BoardModel dict (see B4) |
| `get_round_matrix(circle_id)` | | {members, rounds, contributions} subset |
| `get_member_history(circle_id, member)` | | {late_rounds, payday_rule, commitments, replies} |
| `collect_round(circle_id, round_n)` | | {results: [{member, status, order_id, capture_id, decline_code, label}], queued_position} |
| `charge_vaulted_member(circle_id, round_n, member)` | | one result as above (policy-gated) |
| `get_order_details(circle_id, member, round_n)` | | toolkit `get_order_details` on that contribution's order |
| `get_available_balance(circle_id)` | | {paypal_available: str \| null, circle_available: str, source: "balances" \| "ledger"} |
| `create_pot_payout(circle_id, round_n)` | | {batch_id, item_id, status, amount, recipient, wallet} (policy + mandate gated) |
| `get_payout_status(circle_id, round_n)` | | {status, item_id, batch_id} |
| `record_note(circle_id, text)` | | {ok} (adds a trace line; the only free-text sink) |

Every money tool: `PolicyDenied` -> return `{"ok": false, "denied": reason}` (never raise to the model); PayPalError ->
`{"ok": false, "error": name, "issues": [...], "label": "..."}`. Success -> `{"ok": true, ...}`.

### `app/mcp/public.py` — `paypal_ops_public: FastMCP` with read-only `get_circle`, `get_round_matrix`, `get_charter`,
`get_payout_status` restricted to demo circles (is_fixture_clone) with PII masked (first names only, no emails/phones).
`app/mcp/auth.py` — Starlette middleware: `Authorization: Bearer <settings.mcp_demo_token>` else 401; 30 req/min/token.
`mount_public_mcp(app: FastAPI)` mounts `paypal_ops_public.http_app(path="/mcp")` — B4 calls it from `app/main.py`.

### `app/mcp/strict_schema.py` — `to_anthropic_tool(mcp_tool) -> dict` converting a fastmcp tool (name, description,
inputSchema) into `{"name","description","input_schema","strict": True}` with every property required, optional ->
`{"type": [T, "null"]}`, defaults removed, `additionalProperties: false` recursively. Unit test over every registered tool.

### `app/agent/treasurer.py`

```python
class AgentTask(BaseModel): kind: Literal["collect_round", "payout", "recover"]; circle_id: str; round_n: int; sid: str; context: dict = {}
class AgentRunResult(BaseModel): ok: bool; mode: Literal["live", "replay", "rules"]; summary: str; tool_calls: int; trace: list[dict]
async def run_task(task: AgentTask, *, publish: Callable[[dict], Awaitable[None]]) -> AgentRunResult
```
- Uses `anthropic.AsyncAnthropic()` (reads ANTHROPIC_API_KEY from env via settings; pass `api_key=settings.anthropic_api_key`).
- Model `settings.reasoning_model`; **no `thinking` param, no sampling params, no forced tool_choice** (`{"type":"auto"}`),
  `output_config={"effort": "medium"}`, `max_tokens=8000`, streaming not required for M1.
- Tools: list from `fastmcp.Client(paypal_ops_internal)` in-memory -> `to_anthropic_tool`. Execute via the same Client
  (`await client.call_tool(name, args)`), parse `tool.input` with the SDK object directly (never string-match).
- Loop: messages append-only; handle `stop_reason` in {`tool_use`, `end_turn`, `max_tokens`, `refusal`}; cap 25 tool calls;
  on `refusal` or missing API key -> `app/agent/rules_mode.py: run_rules(task)` which performs the same deterministic
  tool sequence (collect_round -> get_order_details for each COMPLETED -> get_available_balance -> create_pot_payout when
  all COMPLETED) so the demo works with no LLM.
- `publish({"type": "trace", ...})` for every assistant text, tool call and tool result (truncate results to 400 chars).
- System prompt in `app/agent/prompts/treasurer.md`: role, the charter summary (injected), the rules "the model proposes,
  the policy engine decides, humans approve", tool etiquette (one round at a time, confirm captures with get_order_details,
  check balance before payout, stop and explain on any denial). Keep it under 600 words.

Tests (B3): schema converter over all tools; the loop against a fake Anthropic client (monkeypatched `messages.create`
returning a scripted tool_use then end_turn); rules mode end-to-end against B4's engine with a mocked gateway.

**B3 implementation notes (2026-10-22, additive to the above):**
- `app/mcp/internal.py`: tools validate handles before touching the engine (`{"ok": false, "error": "INVALID_INPUT"}`).
  Any other exception becomes `{"ok": false, "error": <ExceptionName>, "message"}` (`ENGINE_UNAVAILABLE` when
  `app.engine` is missing). Engine events reach the session through `bind_publisher(circle_id, publish)` (context
  manager; `run_task`/`run_rules` bind it, tools look the publisher up by `circle_id`). `get_member_history` calls
  `app.engine.state.member_history(circle_id, member)` when B4 provides it, otherwise derives `late_rounds` from the
  board (DECLINED/COVERED or attempt > 1) and `payday_rule` from the charter (`commitments`/`replies` empty in M1).
  `record_note` publishes `{"type": "trace", "kind": "text", "source": "note", "text"}`.
- `app/mcp/public.py`: demo check order: `state.is_fixture_clone(circle_id)` hook if present -> `app.db.session_scope()` +
  `app.models.Circle.is_fixture_clone` -> `board["circle"]["is_fixture_clone"]`; fails closed. Non-demo circles get
  `{"ok": false, "error": "NOT_A_DEMO_CIRCLE"}`. Masking drops email/phone/receiver/vault/customer/sandbox keys, reduces
  `name` to the first name and scrubs email/phone patterns in strings.
- `app/mcp/auth.py`: the public server is served at exactly `/mcp` through `Route("/mcp", endpoint=http_app)` (a plain
  `app.mount` answers `POST /mcp` with a 307 that MCP clients do not follow), stateless streamable HTTP with JSON
  responses, and its lifespan is chained onto the FastAPI app's. Empty `MCP_DEMO_TOKEN` -> 503 "MCP demo token not
  configured"; 30 req/min per (token, IP) -> 429 with `Retry-After`; `X-Forwarded-For` is honoured (Render proxy).
  `mount_public_mcp` is idempotent and must run before the SPA catch-all route (it does, from `_include_routers`).
- `app/agent/treasurer.py`: `run_task(task, *, publish, client=None)` (`client` injectable for tests). Fallback to
  `run_rules` on missing key, `refusal`, `max_tokens`, `model_context_window_exceeded` or any `anthropic.APIError`
  (`mode="rules"`, the live trace is kept in front of the rules trace). The 25-call cap ends the run with `ok=False`,
  `mode="live"`. Trace events carry `mode` ("live"|"rules"); tool results are truncated to 400 chars in the trace only.
- `app/agent/rules_mode.py`: `run_rules(task, *, publish=None, reason="...")`. `collect_round` kind: collect_round ->
  get_order_details per COMPLETED -> get_round_matrix -> get_available_balance -> create_pot_payout only when every
  member is COMPLETED or COVERED -> record_note. `payout` kind skips the collection; `recover` reads the matrix and
  `get_member_history` for every outstanding member and leaves a note (no money tools).

---

## B4 · DB, engine, routes, fixture

### `app/db.py` — `engine = create_engine(f"sqlite:///{settings.db_path}")` (WAL pragma, `:memory:` supported);
`init_db()`; `session_scope()` context manager; `get_session()` FastAPI dependency.

### `app/models.py` (SQLModel, table names = plural snake_case)
- `Session`: sid (pk), run_id, circle_id, created_at, last_seen, live_runs:int=0
- `Circle`: id (pk, 6 chars), run_id, name, charter_json:str, charter_sha256, fee_policy, status
  ("active"|"paused"|"closed"), is_fixture_clone:bool, virtual_now:datetime, created_at
- `MemberRow`: id, circle_id, handle, name, lang, payday_rule, vault_id, customer_id, payout_wallet, payout_receiver,
  sandbox_account (never exposed), rotation_pos:int
- `Round`: id, circle_id, n, due_at, recipient_handle, status ("PENDING"|"COLLECTING"|"COLLECTED"|"PAID"|"VOID"),
  payout_batch_id, payout_item_id, payout_status, rev:int=1, last_capture_at, paid_at
- `Contribution`: id, circle_id, round_n, member_handle, order_id, capture_id, gross:str, fee:str, net:str,
  status ("PENDING"|"COMPLETED"|"DECLINED"|"COVERED"), attempt:int, decline_code, decline_source ("api"|"webhook"|null),
  webhook_verified:bool=False, predicted:bool=False, request_id, created_at, updated_at
- `LedgerRow`: id, circle_id, kind, amount:str, round_n, member_handle, ref
- `MandateRow`: id, circle_id, json:str (signed Mandate), approved_at
- `AgentTrace`: id, circle_id, run_id, ts, kind ("text"|"tool_call"|"tool_result"|"system"), payload:str
- `WebhookEvent`: id, event_id (unique), event_type, resource_id, verified:bool, received_at, payload:str
- `PayPalCall`: id, ts, method, path, status, request_id, ms
- `InboxMessage`, `Remedy`, `Vote`, `LlmCache`, `Metric`: define minimal columns now (id, circle_id, json, ts).

### `app/fixtures/demo_circle.json`
Charter per PLAN.md §3 step 1: "Temple Circle A", 6 members `asha, ravi, meena, tomas, lin, dev`, $200.00 monthly,
start 2026-11-01, rotation `[meena, asha, ravi, dev, lin, tomas]` (meena/asha have track_record_rounds > 0; the rest 0 — note
the collateral rule puts 0-track-record members last, so rotation must satisfy it: adjust to
`[meena, asha, dev, lin, tomas, ravi]`-style ordering as the validator requires), buffer seed 250.00, quorum 4/6.
`seed`: per-member `vault_id` placeholders `"VAULT_ID_<HANDLE>"` overridden at load time from env `TK_SEED_VAULT_IDS`
(JSON {handle: vault_id}) when present. Rounds 1–2 are pre-settled in replay mode with fake IDs prefixed `REPLAY-`.

### `app/engine/events.py`
```python
class EventBus:  # process-wide singleton get_event_bus()
    async def publish(self, sid: str, event: dict) -> None      # event always has "type" and "ts"
    def subscribe(self, sid: str) -> AsyncIterator[dict]          # async generator; heartbeat every 15 s
```
Event types and payloads (the UI depends on these exactly):
- `chip`: {round_n, member, status, order_id?, capture_id?, label?, decline_code?, webhook_verified?, predicted?}
- `trace`: {kind: "text"|"tool_call"|"tool_result"|"system", text, tool?, args?, ms?}
- `payout`: {round_n, status, batch_id?, item_id?, amount, recipient, wallet}
- `ticker`: {last_capture_at, paid_at, seconds}
- `toast`: {level: "info"|"success"|"warn"|"error", text}
- `queue`: {position}
- `board`: {} (tells the UI to refetch the BoardModel)
- `schedule_changed`: {rotation: [handles]}

### `app/engine/sessions.py`
`get_or_create_session(request, response) -> Session` (cookie `tk_sid`, HttpOnly, SameSite=Lax; clones the fixture into a
new Circle with a new `run_id`), `reset_session(sid) -> Session` (new run_id, new clone), `active_count()`, cap at
`settings.max_active_sessions` with 20-min idle TTL -> HTTP 429 `{"waitlist": true}`.

### `app/engine/clock.py` — `VirtualClock(circle)`: `now()`, `advance(days)`; persisted in `Circle.virtual_now`.

### `app/engine/state.py` — `load_charter(circle_id) -> Charter`, `circle_state(circle_id, round_n) -> CircleState`,
`board_model(circle_id) -> dict` (the BoardModel below), `ledger_view(circle_id) -> LedgerView`.

### `app/engine/rounds.py` (called by MCP tools; all policy-gated via `app.domain.policy`)
```python
async def collect_round(circle_id, round_n, *, publish) -> list[dict]
async def charge_member(circle_id, round_n, member, *, publish) -> dict
async def confirm_capture(circle_id, round_n, member, *, publish) -> dict       # toolkit get_order_details
async def available_balance(circle_id) -> dict
async def pay_round(circle_id, round_n, *, publish) -> dict                       # mandate + ledger + balances gates
async def payout_status(circle_id, round_n) -> dict
```
`charge_member` must: build the request id with the member's attempt counter (`a{n}`), call `orders.charge_vaulted`
(mock_decline when `Circle` has judge flag `ravi_declines` and member == "ravi"), persist Contribution + LedgerRow
(net via `capture_summary`), publish `chip`, and map PayPalError INSTRUMENT_DECLINED -> status DECLINED,
decline_source "api", label "API decline: sandbox-injected" when mocked.

### `app/engine/webhooks.py` — `async def handle_event(headers, body: bytes) -> dict`: verify via B1 (unless
`settings.paypal_webhook_id` empty -> store as unverified), dedupe on event id, update Contribution.webhook_verified for
`PAYMENT.CAPTURE.COMPLETED` (match capture id), publish `chip`/`toast`, and when the round's last contribution is
verified and a mandate exists, call `pay_round`.

### Routes
- `GET /api/session` -> {sid, run_id, circle_id, live_ai, paypal_live, frozen}
- `POST /api/session/reset` -> same shape
- `GET /api/circles/{circle_id}` -> BoardModel
- `POST /api/circles/{circle_id}/rounds/{n}/collect` -> {"task_id"} ; runs `run_task(AgentTask(kind="collect_round"))` as an
  asyncio background task; events to the session's SSE stream
- `POST /api/circles/{circle_id}/rounds/{n}/payout` -> same, kind "payout"
- `GET /api/sessions/{sid}/events` -> `text/event-stream` (sse-starlette) of EventBus events
- `POST /api/judge/{action}` with `{circle_id}`; actions: `reset`, `advance_clock` ({days}), `run_round` ({round_n}),
  `toggle_ravi_declines` ({on}), `make_next_payout_unclaimed` ({on}) — later: `reply_as_ravi`, `vote`, `approve`
- `POST /webhooks/paypal` -> `handle_event`; always 200 after persisting
- `GET /api/debug/webhooks` -> last 20 WebhookEvent rows (used by the f_webhook smoke test)
- `app/main.py` additionally calls `app.mcp.auth.mount_public_mcp(app)` inside `_include_routers` (B4 adds that line).

### BoardModel (JSON returned by `GET /api/circles/{id}` and `get_circle`)
```json
{"circle": {"id","name","amount":"200.00","currency":"USD","cadence","charter_sha256","fee_policy","buffer_balance":"250.00",
            "circle_available":"0.00","status","virtual_now","run_id","rotation":["meena",...],"mandate_present":false,
            "flags":{"ravi_declines":true,"next_payout_unclaimed":false}},
 "members": [{"handle","name","lang","payout_wallet","vaulted":true,"rotation_pos":1}],
 "rounds": [{"n":1,"due_at","recipient","status","payout":{"batch_id","item_id","status","amount"}|null,"paid_at"}],
 "contributions": [{"round_n","member","status","order_id","capture_id","gross","fee","net","decline_code",
                    "decline_source","attempt","webhook_verified","predicted","label"}],
 "counters": {"organizer_messages_typed":0,"rounds_paid_on_time":0,"shortfalls_covered":0},
 "ticker": {"last_capture_at":null,"paid_at":null,"seconds":null}}
```

Tests (B4): session isolation (two cookies -> two circles, different run_ids), fixture load + collateral-valid rotation,
collect_round against a mocked Gateway (respx) incl. the mocked decline path, webhook handler dedupe + verification
branch, SSE endpoint yields a published event, BoardModel shape.

**B4 implementation notes (2026-10-23, additive):**
- Routes share one `CurrentSession = Annotated[Session, Depends(require_session)]` (401 without the `tk_sid`
  cookie; `GET /api/session` creates it). Collect/payout answer `202 {"task_id", "kind", "circle_id", "round_n",
  "agent"}`; `GET /api/tasks/{task_id}` reports the background run (`done`, `ok`, `mode`, `summary`, `error`). A
  second run on the same circle is `409` with the running `task_id`. The run is `asyncio` task
  `app.engine.tasks._run` -> `app.agent.treasurer.run_task(AgentTask(...), publish=<session publisher>)`; every
  `trace` event is also persisted as an `AgentTrace` row. Judge `run_round` falls back to the engine when the agent
  package is missing (`mode="engine"`).
- `BoardModel.circle` additionally carries `frozen` and `is_fixture_clone`; `ticker` follows the highest round with a
  capture (replayed rounds carry virtual timestamps that may lie after the wall clock).
- `app.engine.rounds` reaches PayPal only through `_gateway()/_orders()/_payouts()/_balances()/_toolkit()`; tests
  monkeypatch those (`tests/engine/fakes.py: FakePayPal`) and never use the network. `tests/conftest.py` sets
  `TK_DB_PATH=:memory:` and `app.db` uses a `StaticPool` so the TestClient thread and background tasks share it.
- SSE: Starlette's `TestClient` buffers responses until the app returns, so `tests/routes/test_events_route.py`
  drives the ASGI app directly (publish on the bus, read the frame, send `http.disconnect`).

---

## B5 · `web/` (Vite + React + TypeScript)

- `npm create vite@latest` equivalent layout; `vite.config.ts`: `build.outDir = "../app/static"`, `emptyOutDir: true`,
  dev proxy `/api`, `/webhooks`, `/mcp` -> `http://localhost:8000`.
- Bryntum: `npm install @bryntum/scheduler@npm:@bryntum/scheduler-trial @bryntum/scheduler-react@npm:@bryntum/scheduler-react-trial`.
  If install fails, implement `RotationBoard` as a CSS grid with the same props and leave a TODO; never block the build.
- Components (`web/src/components/`): `RotationBoard` (resources = members in rotation order, time axis = rounds by
  due date, one event per contribution coloured by status, a "pot" milestone on the recipient's row; hover shows order
  and capture IDs), `AgentTrace` (scrolling list of `trace` events), `JudgeBar` (buttons calling `/api/judge/*` and
  `/api/circles/{id}/rounds/{n}/collect`), `PayoutReceipt`, `Ticker`, `Counters`, `QueueBadge`, `CharterCard`.
- `web/src/api.ts`: typed fetchers for the routes above; `web/src/events.ts`: `EventSource` to `/api/sessions/{sid}/events`
  with reconnect; on `board` refetch the BoardModel.
- Light/dark via CSS tokens; phone-width layout for `/join` later. No external fonts or CDNs; all assets bundled.
- `npm run build` must succeed on Node 20 with no network at build time other than npm install.

**B5 implementation notes (2026-10-23, additive; recorded by the integrator on F5's behalf):**
- The Bryntum React wrapper is `@bryntum/scheduler-react@7.3.7` (exact; no `-trial` variant of the wrapper
  exists) next to the aliased `@bryntum/scheduler-trial@7.3.7`; `web/.npmrc` pins the `@bryntum` registry.
- `RotationBoard` lives in `web/src/board/` (`mapping.ts` pure + tested, `SchedulerBoard.tsx` Bryntum,
  `GridBoard.tsx` CSS-grid fallback, `RotationBoard.tsx` dynamic import + error boundary), not `src/components/`.
- The trial watermark is expected; if the trial throws at runtime the grid fallback takes over automatically.

---

## Integration notes (2026-10-23, additive)

- `app/db.py: upgrade_schema()` runs inside `init_db()`: columns the models gained since a file database was
  created are added with `ALTER TABLE ... ADD COLUMN` (nullable, additive only). A stale `data/turnkeeper.db`
  (missing `contributions.last_attempt_at`) therefore no longer 500s on `/api/session`; no manual delete needed.
- `tests/integration/test_m1_flow.py` drives the real app through `TestClient` with the sandbox mocked by respx
  (token, orders create/get, balances, payouts create/batch/item) in rules mode. The toolkit's `get_order_details`
  (sync `requests`, invisible to respx) is replaced there by a shim that performs the same GET through the gateway's
  httpx path and returns the toolkit's own `{"message","status","amount"}` shape. Note that a rules-mode
  `collect_round` run already pays the pot when every member is COMPLETED, so a following `POST .../payout` is
  accepted (202), runs, and is refused with "round 3 is already paid" without a second Payouts call.

---

## Shared conventions

- IDs: `circle_id` 6 lowercase alphanumerics; `run_id` 8-char base32 (keys.new_run_id). Member handles are the only
  member identifiers in tool inputs.
- Status vocab: contributions `PENDING|COMPLETED|DECLINED|COVERED`; rounds `PENDING|COLLECTING|COLLECTED|PAID|VOID`;
  payout items use PayPal's strings verbatim.
- Every tool/engine result dict has `"ok": bool` first.
- Timestamps ISO-8601 UTC strings on the wire.
- Tests must pass with `pytest -q` from the repo root using the venv; `ruff check app tests scripts` must be clean.
