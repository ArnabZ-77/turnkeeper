/**
 * Member join page, phone first (PLAN.md section 3 step 5; docs/CONTRACTS.md "Builder B").
 * Pre-consent charter card -> "Continue to PayPal" (Vault setup token, approve once) -> PayPal returns to
 * `/join/{invite}/return` on the server, which answers 303 to `/join/{invite}?status=...` -> this page
 * shows the outcome and, once vaulted, the payout wallet choice.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, describeError } from "../api";
import { billingPlanLines, feeLine, shortHash } from "../compile";
import { fmtDate, fmtMoney } from "../format";
import { isPayPalApproveUrl, joinStatusView, statusForLoadError, walletEmailProblem } from "../join";
import type { JoinCard } from "../types";
import { IconCheck, IconShield } from "../components/icons";
import { ThemeToggle } from "../components/Link";

export interface JoinPageProps {
  invite: string;
  status: string | null;
}

const REMEDY: Record<string, string> = {
  retry_on_date: "retry on your payday",
  turn_swap: "swap turns (only with your consent)",
  buffer_advance: "the organizer's buffer covers you",
  repay_by_invoice: "repay by PayPal invoice",
};

function startError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) return "PayPal sandbox is not configured on this server, so approval cannot start.";
    const body = e.body as { detail?: { message?: unknown } } | null;
    const msg = body?.detail?.message;
    if (typeof msg === "string") return msg;
  }
  return describeError(e);
}

export function JoinPage({ invite, status }: JoinPageProps) {
  const [card, setCard] = useState<JoinCard | null>(null);
  const [loadStatus, setLoadStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "start" | "wallet">(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [walletDone, setWalletDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCard(await api.join.card(invite));
      setLoadStatus(null);
    } catch (e) {
      setLoadStatus(e instanceof ApiError ? statusForLoadError(e.status) : "error");
      if (!(e instanceof ApiError)) setError(describeError(e));
    }
  }, [invite]);

  useEffect(() => {
    void load();
  }, [load]);

  const view = joinStatusView(card, loadStatus ?? status);
  const c = card?.circle;
  const currency = c?.currency ?? "USD";

  const onContinue = async () => {
    setBusy("start");
    setError(null);
    try {
      const r = await api.join.start(invite);
      if (!isPayPalApproveUrl(r.approve_url)) {
        setError("PayPal did not return a PayPal approval link. Nothing was saved; please try again.");
        return;
      }
      window.location.assign(r.approve_url);
    } catch (e) {
      setError(startError(e));
      void load();
    } finally {
      setBusy(null);
    }
  };

  const hasDefault = Boolean(card?.join.payout_receiver_masked);
  const emailProblem = walletEmailProblem(email, hasDefault);
  const onWallet = async () => {
    if (emailProblem) return;
    setBusy("wallet");
    setError(null);
    try {
      const r = await api.join.wallet(invite, { wallet: "PAYPAL", receiver: email.trim() || undefined });
      setWalletDone(r.payout_receiver_masked ?? "your PayPal account");
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const step = view.status === "vaulted" ? (walletDone ? 4 : 3) : view.status === "pending_approval" ? 2 : 1;
  // A dead invite has no steps left to take: no stepper, and a way back to the demo.
  const dead = view.status === "invalid" || view.status === "expired";

  return (
    <div className="tk-join">
      <header className="tk-join-head">
        <span className="tk-join-brand">Turnkeeper</span>
        <span className="tk-chip tk-chip-warn" title="PayPal sandbox: test accounts only, no real money moves">
          <span className="tk-dot" />
          sandbox
        </span>
        <span className="tk-spacer" />
        <ThemeToggle />
      </header>

      {!dead && (
      <ol className="tk-join-steps" aria-label="Join steps">
        {["Review", "Approve on PayPal", "Payout wallet"].map((label, i) => (
          <li key={label} className={i + 1 < step ? "tk-done" : i + 1 === step ? "tk-current" : ""}>
            <span className="tk-join-step-n">{i + 1 < step ? <IconCheck /> : i + 1}</span>
            {label}
          </li>
        ))}
      </ol>
      )}

      <div className={`tk-join-status tk-tone-${view.tone}`} role="status">
        <strong>{view.title}</strong>
        <span>{view.body}</span>
        {dead && (
          <a className="tk-btn tk-join-back" href="/">
            Open the demo board
          </a>
        )}
      </div>

      {error && (
        <div className="tk-join-status tk-tone-critical" role="alert">
          <span>{error}</span>
        </div>
      )}

      {!card && !loadStatus && <p className="tk-muted tk-join-loading">Loading the circle…</p>}

      {card && c && (
        <section className="tk-card tk-join-card" aria-label="Circle charter">
          <p className="tk-muted tk-small">
            {card.member.name.split(/\s+/)[0]}, {view.status === "vaulted" ? "you're a member of" : "you're invited to"}
          </p>
          <h1 className="tk-join-title">{c.name}</h1>
          <p className="tk-join-amount">
            {fmtMoney(c.amount, currency)} <span>{c.cadence} × {c.cycles} rounds</span>
          </p>
          <dl className="tk-kv tk-join-kv">
            <dt>Starts</dt>
            <dd>{fmtDate(c.start_date)}</dd>
            <dt>Your pot</dt>
            <dd>
              {card.join.payout_round
                ? `Round ${card.join.payout_round}${card.join.payout_date ? `, ${fmtDate(card.join.payout_date)}` : ""}`
                : "—"}
            </dd>
            <dt>Fees</dt>
            <dd>
              <strong>{feeLine(c.fees, currency)}</strong>
              <span className="tk-small tk-muted tk-block">{c.fee_note}</span>
            </dd>
            <dt>Members</dt>
            <dd>{c.members.map((m) => m.name).join(", ")}</dd>
            <dt>Decisions</dt>
            <dd>
              {c.quorum.replace("/", " of ")} votes · {c.grace_days}-day grace · ${c.buffer_seed} organizer buffer
            </dd>
            <dt>If short</dt>
            <dd>{c.remedy_menu.map((r) => REMEDY[r] ?? r).join("; ")}</dd>
          </dl>
          <div className="tk-join-plan">
            <span className="tk-small tk-muted">What PayPal will show you</span>
            {billingPlanLines(c.billing_plan).map((l) => (
              <span key={l}>{l}</span>
            ))}
          </div>
          <p className="tk-join-consent">{card.consent}</p>
          <span className="tk-chip tk-chip-hash" title={`Charter SHA-256: ${c.charter_sha256}`}>
            <IconShield /> charter <code>{shortHash(c.charter_sha256)}</code>
          </span>
        </section>
      )}

      {card && view.canContinue && (
        <div className="tk-join-cta">
          <button
            type="button"
            className="tk-btn tk-btn-primary tk-btn-block"
            onClick={() => void onContinue()}
            disabled={busy !== null || !card.paypal_ready}
            title={card.paypal_ready ? undefined : "PayPal sandbox credentials are not configured on this server"}
          >
            {busy === "start" ? "Opening PayPal…" : view.status === "invited" ? "Continue to PayPal" : "Try PayPal again"}
          </button>
          <span className="tk-small tk-muted">
            {card.paypal_ready
              ? "You approve the schedule once on PayPal. Nothing is charged today."
              : "PayPal sandbox is not configured on this server."}
          </span>
        </div>
      )}

      {card && view.showWallet && (
        <section className="tk-card tk-join-card" aria-label="Payout wallet">
          <h2 className="tk-card-title">Where should your pot go?</h2>
          <div className="tk-wallets" role="radiogroup" aria-label="Payout wallet">
            <label className="tk-wallet tk-wallet-on">
              <input type="radio" name="wallet" checked readOnly /> PayPal
            </label>
            <label className="tk-wallet tk-wallet-off" title="Venmo payouts are switched off in this sandbox demo">
              <input type="radio" name="wallet" disabled /> Venmo <span className="tk-small tk-muted">not in this demo</span>
            </label>
          </div>
          <label className="tk-field">
            <span className="tk-small">PayPal email for payouts</span>
            <input
              type="email"
              inputMode="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={hasDefault ? `keep ${card.join.payout_receiver_masked}` : "you@example.com"}
            />
          </label>
          {email && emailProblem && <span className="tk-small tk-critical-text">{emailProblem}</span>}
          <button type="button" className="tk-btn tk-btn-block" onClick={() => void onWallet()} disabled={busy !== null || Boolean(emailProblem)}>
            {busy === "wallet" ? "Saving…" : "Save payout wallet"}
          </button>
          {walletDone && (
            <span className="tk-small tk-good-text">
              <IconCheck /> Pot goes to PayPal {walletDone}.
            </span>
          )}
        </section>
      )}

      <p className="tk-small tk-muted tk-join-foot">
        Turnkeeper runs on the PayPal sandbox. Charges follow the frozen charter; any change needs a new charter and your
        fresh consent.
      </p>
    </div>
  );
}
