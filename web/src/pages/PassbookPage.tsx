/**
 * Member Passbook, phone first (flag TK_PASSBOOK). One member's own money, from stored state only:
 * payments with their PayPal capture ids (each expands into the chain of authority behind it), this round
 * (counts only: nobody else is named), my turn, my fees, and what the frozen charter does if someone misses.
 * The capability is in the URL fragment (`/m#tkc1...`) and goes to the API in a header.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { fmtMoney } from "../format";
import { REMEDY_WORDS, roundLine, tokenFromHash, turnLine, words, type Lang } from "../passbook";
import type { PassbookView } from "../types";
import { IconCheck, IconShield } from "../components/icons";

function Receipt({ steps }: { steps: PassbookView["payments"][number]["receipt"] }) {
  return (
    <ol className="tk-pb-chain">
      {steps.map((s) => (
        <li key={s.step}>
          <span className="tk-pb-chain-label">{s.label}</span>
          {s.value && <code className="tk-pb-chain-value">{s.value}</code>}
        </li>
      ))}
    </ol>
  );
}

export function PassbookPage() {
  const [token, setToken] = useState(() => tokenFromHash(window.location.hash));
  const [view, setView] = useState<PassbookView | null>(null);
  const [error, setError] = useState<"expired" | "off" | null>(token ? null : "expired");
  const [lang, setLang] = useState<Lang>("en");
  const [open, setOpen] = useState<number | null>(null);
  const [qr, setQr] = useState<string | null>(null);

  // Opening another member's link in the same tab changes only the fragment: no reload, so follow it here.
  useEffect(() => {
    const onHash = () => setToken(tokenFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    setView(null);
    setQr(null);
    setOpen(null);
    setError(token ? null : "expired");
    if (!token) return;
    let live = true;
    api.passbook
      .get(token)
      .then((v) => {
        if (!live) return;
        setView(v);
        setLang(v.member.lang === "hi" ? "hi" : "en");
      })
      .catch((e) => live && setError(e instanceof ApiError && e.status === 404 ? "off" : "expired"));
    return () => {
      live = false;
    };
  }, [token]);

  useEffect(() => {
    if (!token || !view?.repayment?.qr) return;
    let url: string | null = null;
    void api.passbook.qr(token).then((blob) => {
      if (blob) {
        url = URL.createObjectURL(blob);
        setQr(url);
      }
    });
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [token, view?.repayment?.qr]);

  const w = words(lang);
  if (error) {
    return (
      <main className="tk-pb">
        <p className="tk-callout tk-callout-critical">{error === "off" ? w.off : w.expired}</p>
      </main>
    );
  }
  if (!view) return <main className="tk-pb" aria-busy="true" />;
  const cur = view.circle.currency;
  const turn = turnLine(view, lang);
  return (
    <main className="tk-pb" lang={lang}>
      <header className="tk-pb-head">
        <div>
          <h1 className="tk-pb-title">{view.member.name}</h1>
          <p className="tk-muted tk-small">
            {view.circle.name} · {fmtMoney(view.circle.amount, cur)} / {view.circle.cadence}
          </p>
        </div>
        <div className="tk-pb-lang" role="group" aria-label="Language">
          <button type="button" className="tk-btn" aria-pressed={lang === "en"} onClick={() => setLang("en")}>
            EN
          </button>
          <button type="button" className="tk-btn" aria-pressed={lang === "hi"} onClick={() => setLang("hi")}>
            हिं
          </button>
        </div>
      </header>
      <p className="tk-chip tk-chip-warn tk-pb-demo">
        <IconShield /> {w.demo}
      </p>

      {view.repayment && (
        <section className="tk-card tk-pb-card" aria-label={w.repay}>
          <h2 className="tk-card-title">{w.repay}</h2>
          <p>
            <strong>{view.repayment.amount ? fmtMoney(view.repayment.amount, cur) : "—"}</strong>
            {view.repayment.due_date ? ` · ${view.repayment.due_date}` : ""} · {view.repayment.status ?? ""}
            {view.repayment.simulated ? " (simulated)" : ""}
          </p>
          {qr && (
            <figure className="tk-pb-qr">
              <img src={qr} alt={w.payNow} width={160} height={160} />
              <figcaption className="tk-small tk-muted">{w.payNow}</figcaption>
            </figure>
          )}
        </section>
      )}

      {view.this_round && (
        <section className="tk-card tk-pb-card" aria-label={w.thisRound}>
          <h2 className="tk-card-title">
            {w.thisRound} · {view.this_round.round_n}
          </h2>
          {roundLine(view, lang).map((line) => (
            <p key={line}>{line}</p>
          ))}
        </section>
      )}

      <section className="tk-card tk-pb-card" aria-label={w.myTurn}>
        <h2 className="tk-card-title">{w.myTurn}</h2>
        {turn && <p>{turn}</p>}
        <p className="tk-small">
          {w.expected}: <strong>{fmtMoney(view.turn.payout_amount ?? view.turn.expected_pot, cur)}</strong>
          {view.turn.payout_status ? ` · ${view.turn.payout_status}` : ""}
          {view.turn.payout_batch_id ? (
            <>
              {" "}
              · <code>{view.turn.payout_batch_id}</code>
            </>
          ) : null}
        </p>
      </section>

      <section className="tk-card tk-pb-card" aria-label={w.payments}>
        <h2 className="tk-card-title">{w.payments}</h2>
        <ul className="tk-pb-pays">
          {view.payments.map((p) => (
            <li key={p.round_n}>
              <button
                type="button"
                className="tk-pb-pay"
                aria-expanded={open === p.round_n}
                onClick={() => setOpen(open === p.round_n ? null : p.round_n)}
              >
                <span>R{p.round_n}</span>
                <span>{p.gross ? fmtMoney(p.gross, cur) : "—"}</span>
                <span className={p.status === "DECLINED" ? "tk-pb-bad" : "tk-pb-good"}>
                  {p.status !== "DECLINED" && <IconCheck />} {w.status[p.status] ?? p.status}
                </span>
                {p.simulated && <span className="tk-chip tk-chip-muted tk-pb-replay">{w.replay}</span>}
                {p.capture_id && <code className="tk-pb-id">{p.capture_id}</code>}
              </button>
              {open === p.round_n && (
                <div className="tk-pb-receipt">
                  <p className="tk-small tk-muted">{w.receipt}</p>
                  {p.simulated && <p className="tk-small tk-pb-replay-note">{w.replayNote}</p>}
                  <Receipt steps={p.receipt} />
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="tk-card tk-pb-card" aria-label={w.fees}>
        <h2 className="tk-card-title">{w.fees}</h2>
        <p className="tk-small">
          {w.feesPaid}: <strong>{fmtMoney(view.fees.paid, cur)}</strong> · {w.perPayment}:{" "}
          {fmtMoney(view.fees.per_payment, cur)}
        </p>
      </section>

      <section className="tk-card tk-pb-card" aria-label={w.misses}>
        <h2 className="tk-card-title">{w.misses}</h2>
        <ul className="tk-small">
          <li>
            {w.buffer}: {fmtMoney(view.if_someone_misses.buffer, cur)}
          </li>
          <li>
            {w.menu}: {view.if_someone_misses.remedy_menu.map((r) => REMEDY_WORDS[lang][r] ?? r).join(" · ")}
          </li>
          <li>
            {w.quorum}: {view.if_someone_misses.quorum} · {w.grace}: {view.if_someone_misses.grace_days}
          </li>
          <li>
            {w.hash}: <code>{view.if_someone_misses.charter_sha256.slice(0, 12)}…</code>
          </li>
        </ul>
      </section>
    </main>
  );
}
