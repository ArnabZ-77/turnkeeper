/**
 * Charter compiler (PLAN.md section 3 steps 1-3; docs/CONTRACTS.md "Builder B").
 * Paste the circle's rules (the demo's Hindi text is prefilled, English one tap away) -> compile -> a
 * charter card with the compiler's explanation and problems per field -> freeze (hash, fees, the billing
 * plan PayPal will show) -> the organizer's one-tap confirmation.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, describeError } from "../api";
import { billingPlanLines, charterRows, compileModeChip, feeLine, generalProblems, shortHash } from "../compile";
import { copyText, fmtDate, fmtDateTime } from "../format";
import type { CompileResult, FreezeResult, SessionInfo } from "../types";
import { IconCheck, IconCopy, IconShield } from "../components/icons";
import { Link, ThemeToggle } from "../components/Link";

type Lang = "hi" | "en";

function HashChip({ sha, label = "charter" }: { sha: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="tk-chip tk-chip-hash"
      title={`SHA-256 of the canonical charter JSON: ${sha} (click to copy)`}
      onClick={async () => {
        if (await copyText(sha)) {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }
      }}
    >
      <IconShield /> {label} <code>{shortHash(sha)}</code> {copied ? <IconCheck /> : <IconCopy />}
    </button>
  );
}

export function CompilePage() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [lang, setLang] = useState<Lang>("hi");
  const [text, setText] = useState("");
  const [result, setResult] = useState<CompileResult | null>(null);
  const [freeze, setFreeze] = useState<FreezeResult | null>(null);
  const [busy, setBusy] = useState<null | "demo" | "compile" | "freeze" | "confirm">(null);
  const [error, setError] = useState<string | null>(null);
  const demoCache = useRef<Partial<Record<Lang, string>>>({});

  const loadDemo = useCallback(async (l: Lang) => {
    setLang(l);
    if (demoCache.current[l]) {
      setText(demoCache.current[l] ?? "");
      return;
    }
    setBusy("demo");
    try {
      const d = await api.compile.demo(l);
      demoCache.current[l] = d.text;
      setText(d.text);
    } catch (e) {
      setError(`Could not load the example: ${describeError(e)}`);
    } finally {
      setBusy(null);
    }
  }, []);

  useEffect(() => {
    api
      .getSession()
      .then(setSession)
      .catch((e) =>
        setError(e instanceof ApiError && e.waitlist ? "All demo seats are busy right now; try again in a minute." : `Could not load the session: ${describeError(e)}`),
      );
    void loadDemo("hi");
  }, [loadDemo]);

  const onCompile = async () => {
    setBusy("compile");
    setError(null);
    setFreeze(null);
    try {
      setResult(await api.compile.run(text, lang));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const onFreeze = async (confirm: boolean) => {
    if (!session || !result) return;
    setBusy(confirm ? "confirm" : "freeze");
    setError(null);
    try {
      setFreeze(await api.compile.freeze(session.circle_id, { draft_id: result.draft_id, confirm }));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const rows = result ? charterRows(result) : [];
  const general = result ? generalProblems(result.problems) : [];
  const chip = result ? compileModeChip(result) : null;
  const currency = result?.charter?.currency ?? "USD";

  return (
    <div className="tk-page">
      <header className="tk-page-head">
        <div className="tk-brand">
          <h1>Turnkeeper</h1>
          <span className="tk-tagline">Charter compiler: the circle's rules, as a frozen, hashed charter</span>
        </div>
        <div className="tk-chips">
          <Link className="tk-chip" href="/">
            ← Board
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {error && (
        <div className="tk-banner tk-banner-error" role="alert">
          {error}
        </div>
      )}

      <div className="tk-compile">
        <section className="tk-card tk-compile-input" aria-label="Circle rules">
          <h2 className="tk-card-title">
            <span>1 · Paste the circle's rules</span>
            <span className="tk-seg" role="group" aria-label="Example language">
              <button type="button" aria-pressed={lang === "hi"} onClick={() => void loadDemo("hi")} disabled={busy !== null}>
                हिंदी
              </button>
              <button type="button" aria-pressed={lang === "en"} onClick={() => void loadDemo("en")} disabled={busy !== null}>
                English
              </button>
            </span>
          </h2>
          <textarea
            className="tk-textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
            lang={lang}
            maxLength={4000}
            spellCheck={false}
            aria-label="Rules text"
            placeholder={busy === "demo" ? "Loading the example…" : "Paste the rules your circle already agreed on"}
          />
          <p className="tk-muted tk-small tk-privacy-note" data-testid="privacy-note">
            Privacy: this is a sandbox demo, so use made-up names. When live AI is on, the text you paste may be sent to a
            third-party model (Google Gemini free tier, which may use it to improve its models). Emails and phone numbers
            are masked first. See PRIVACY.md.
          </p>
          <div className="tk-row">
            <span className="tk-muted tk-small">{text.length} / 4000 · rules text is treated as data, never as instructions</span>
            <span className="tk-spacer" />
            <button type="button" className="tk-btn tk-btn-primary" onClick={() => void onCompile()} disabled={busy !== null || !text.trim()}>
              {busy === "compile" ? "Compiling…" : "Compile charter"}
            </button>
          </div>
        </section>

        <section className="tk-card tk-compile-card" aria-label="Compiled charter" aria-busy={busy === "compile"}>
          <h2 className="tk-card-title">
            <span>2 · Charter{result?.charter ? ` · ${result.charter.name}` : ""}</span>
            {chip && (
              <span className={`tk-chip ${chip.cls}`} title={chip.tip}>
                <span className="tk-dot" />
                {chip.label}
              </span>
            )}
          </h2>
          {!result && <p className="tk-muted">Compile the rules to see each charter field, why the compiler chose it, and anything it had to assume.</p>}
          {result && !result.ok && (
            <div className="tk-callout tk-callout-critical" role="alert">
              Not a valid charter yet. Fix the rules text and compile again.
            </div>
          )}
          {general.length > 0 && (
            <ul className="tk-problems">
              {general.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {rows.length > 0 && (
            <dl className="tk-charter-rows">
              {rows.map((r) => (
                <div key={r.field} className={`tk-charter-row${r.problems.length ? " tk-has-problem" : ""}`}>
                  <dt>{r.label}</dt>
                  <dd>
                    <span className="tk-charter-value">{r.value}</span>
                    {r.explanation && (
                      <span className="tk-charter-why" title={r.fullExplanation ?? r.explanation}>
                        {r.explanation}
                      </span>
                    )}
                    {r.problems.map((p) => (
                      <span key={p} className="tk-charter-problem">
                        {p}
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {result && result.notes.length > 0 && <p className="tk-small tk-muted">{result.notes.join(" ")}</p>}
          {result?.ok && (
            <div className="tk-row">
              {result.charter_sha256 && <HashChip sha={result.charter_sha256} label="draft" />}
              {result.matches_circle && (
                <span className="tk-chip tk-chip-good" title="Byte-for-byte the same charter as the demo circle (same SHA-256)">
                  <span className="tk-dot" /> same as the demo circle
                </span>
              )}
              <span className="tk-spacer" />
              {freeze ? (
                <span className="tk-chip tk-chip-good">
                  <IconCheck /> Frozen
                </span>
              ) : (
                <button type="button" className="tk-btn tk-btn-primary" onClick={() => void onFreeze(false)} disabled={busy !== null || !session}>
                  {busy === "freeze" ? "Freezing…" : "Freeze charter"}
                </button>
              )}
            </div>
          )}
        </section>

        <section className="tk-card tk-compile-freeze" aria-label="Frozen charter">
          <h2 className="tk-card-title">
            <span>3 · Freeze and approve</span>
            {freeze?.confirmed && (
              <span className="tk-chip tk-chip-good">
                <IconCheck /> approved
              </span>
            )}
          </h2>
          {!freeze && <p className="tk-muted">Freezing fixes the charter's canonical JSON and hash. Any later change is a new charter and needs every member's fresh consent.</p>}
          {freeze && (
            <div className="tk-enter">
              <div className="tk-row">
                <HashChip sha={freeze.charter_sha256} />
              </div>
              <p className="tk-fee-line">{feeLine(freeze.fees, currency)}</p>
              <p className="tk-small tk-muted">{freeze.fees.line}</p>
              <h3 className="tk-subhead">Billing plan PayPal shows each member</h3>
              <ul className="tk-plan">
                {billingPlanLines(freeze.billing_plan).map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
              <dl className="tk-kv">
                <dt>Rounds</dt>
                <dd>
                  {freeze.summary.cycles} · {fmtDate(freeze.summary.start_date)}
                  {freeze.summary.last_due_date ? ` → ${fmtDate(freeze.summary.last_due_date)}` : ""}
                </dd>
                <dt>Quorum</dt>
                <dd>{freeze.summary.quorum}</dd>
                <dt>Buffer</dt>
                <dd>${freeze.summary.buffer_seed}</dd>
              </dl>
              {!freeze.confirmed ? (
                <div className="tk-row">
                  <span className="tk-muted tk-small">The organizer approves once; members consent on PayPal.</span>
                  <span className="tk-spacer" />
                  <button type="button" className="tk-btn tk-btn-primary" onClick={() => void onFreeze(true)} disabled={busy !== null}>
                    {busy === "confirm" ? "Approving…" : "Approve as Priya (organizer)"}
                  </button>
                </div>
              ) : (
                <div className="tk-callout tk-callout-good">
                  {freeze.approval
                    ? `Approved by ${freeze.approval.approved_by} at ${fmtDateTime(freeze.approval.approved_at)}.`
                    : "Approved."}{" "}
                  {freeze.created_circle
                    ? "A new circle was created for this charter; the demo board stays as it is."
                    : freeze.matches_circle
                      ? "This is the demo circle's charter."
                      : ""}
                </div>
              )}
              {freeze.invites && freeze.invites.length > 0 && (
                <>
                  <h3 className="tk-subhead">Member invites (signed, expiring)</h3>
                  <ul className="tk-invites">
                    {freeze.invites.map((i) => (
                      <li key={i.handle}>
                        <strong>{i.name}</strong>
                        <span className={`tk-chip ${i.vaulted ? "tk-chip-good" : "tk-chip-pending"}`}>
                          <span className="tk-dot" />
                          {i.join_status}
                        </span>
                        <Link href={`/join/${encodeURIComponent(i.token)}`}>open invite</Link>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
