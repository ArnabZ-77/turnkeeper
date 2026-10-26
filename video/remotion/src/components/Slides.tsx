import React from 'react';
import {AbsoluteFill, Easing, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';
import {captured, lineAt, wordAt, type SbScene} from '../film-data';
import {useSceneT, useTIn} from '../scene-context';
import {LowerThird} from './Overlays';

/** 0..1 spring that starts when scene time reaches t0 (seconds). */
export const useReveal = (t0: number, damping = 200) => {
  const {fps} = useVideoConfig();
  const t = useSceneT();
  if (t < t0) return 0;
  return spring({frame: Math.round((t - t0) * fps), fps, config: {damping, mass: 0.9}});
};

export const Backdrop: React.FC = () => {
  const frame = useCurrentFrame();
  const drift = Math.sin(frame / 150) * 40;
  return (
    <AbsoluteFill style={{background: `radial-gradient(1300px 800px at ${28 + drift / 40}% 12%, ${brand.navy2} 0%, ${brand.navy} 62%)`}}>
      <AbsoluteFill
        style={{
          backgroundImage: 'radial-gradient(rgba(255,255,255,0.07) 1.4px, transparent 1.6px)',
          backgroundSize: '48px 48px',
          backgroundPosition: `${drift}px ${drift / 2}px`,
          maskImage: 'linear-gradient(180deg, rgba(0,0,0,.9), rgba(0,0,0,.15))',
        }}
      />
      <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: 14, background: `linear-gradient(180deg, ${brand.blue}, ${brand.accent})`}} />
    </AbsoluteFill>
  );
};

const H1: React.FC<{children: React.ReactNode; t0?: number; size?: number; top?: number}> = ({children, t0 = 0.15, size = 70, top = 96}) => {
  const p = useReveal(t0);
  return (
    <div
      style={{
        position: 'absolute',
        left: 120,
        right: 120,
        top,
        fontFamily: brand.font,
        fontWeight: 800,
        fontSize: size,
        letterSpacing: -1,
        color: brand.white,
        opacity: p,
        translate: `${(1 - p) * -40}px 0`,
      }}
    >
      {children}
    </div>
  );
};

const Rise: React.FC<{t0: number; children: React.ReactNode; style?: React.CSSProperties}> = ({t0, children, style}) => {
  const p = useReveal(t0);
  return <div style={{opacity: p, translate: `0 ${(1 - p) * 34}px`, ...style}}>{children}</div>;
};

const Icon: React.FC<{kind: 'bank' | 'ledger' | 'shield'; size?: number; color?: string}> = ({kind, size = 96, color = brand.accent}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none" stroke={color} strokeWidth={3.5} strokeLinejoin="round" strokeLinecap="round">
    {kind === 'bank' && (
      <>
        <path d="M6 24 32 8l26 16z" />
        <path d="M12 28v20M24 28v20M40 28v20M52 28v20M6 52h52M6 58h52" />
      </>
    )}
    {kind === 'ledger' && (
      <>
        <rect x="12" y="8" width="40" height="48" rx="5" />
        <path d="M20 22h24M20 32h24M20 42h14" />
        <path d="m38 44 5 5 9-11" stroke={brand.good} />
      </>
    )}
    {kind === 'shield' && (
      <>
        <path d="M32 6 10 14v16c0 14 9 22 22 28 13-6 22-14 22-28V14z" />
        <path d="m22 32 7 7 14-15" />
      </>
    )}
  </svg>
);

/* ---------- s01 hook ---------- */
export const HookSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const tBank = wordAt(sc, 'L01', 'bank');
  const tColl = wordAt(sc, 'L01', 'collector');
  const tIns = wordAt(sc, 'L01', 'insurer');
  const rows: {t: number; text: string; icon: 'bank' | 'ledger' | 'shield'}[] = [
    {t: tBank - 0.25, text: 'the bank.', icon: 'bank'},
    {t: tColl - 0.3, text: 'the debt collector.', icon: 'ledger'},
    {t: tIns - 0.3, text: 'the insurer.', icon: 'shield'},
  ];
  return (
    <AbsoluteFill>
      <Backdrop />
      <Rise t0={0.2} style={{position: 'absolute', left: 150, top: 190, fontFamily: brand.font, fontWeight: 600, fontSize: 62, color: brand.mute}}>
        In a savings circle, someone has to be
      </Rise>
      {rows.map((r, i) => (
        <HookRow key={i} {...r} y={300 + i * 150} />
      ))}
    </AbsoluteFill>
  );
};
const HookRow: React.FC<{t: number; text: string; icon: 'bank' | 'ledger' | 'shield'; y: number}> = ({t, text, icon, y}) => {
  const p = useReveal(t, 18);
  return (
    <div style={{position: 'absolute', left: 150, top: y, display: 'flex', alignItems: 'center', gap: 36, opacity: Math.min(1, p * 1.5), translate: `${(1 - p) * 90}px 0`}}>
      <div style={{width: 112, height: 112, borderRadius: 28, background: 'rgba(245,166,35,0.12)', border: `2px solid ${brand.accent}`, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
        <Icon kind={icon} size={72} />
      </div>
      <div style={{fontFamily: brand.font, fontWeight: 800, fontSize: 118, letterSpacing: -2, color: brand.white}}>{text}</div>
    </div>
  );
};

/* ---------- s03 title card ---------- */
export const TitleSlide: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const f = Math.max(0, frame - tIn);
  const t = spring({frame: f, fps, config: {damping: 14, stiffness: 120, mass: 0.9}});
  const bar = spring({frame: f - 6, fps, config: {damping: 30, stiffness: 120}});
  const steps = ['consent once', 'predict', 'recover', 'pay out'];
  return (
    <AbsoluteFill>
      <Backdrop />
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: brand.font, color: brand.white}}>
        <div style={{textAlign: 'center', maxWidth: 1600}}>
          <div style={{fontSize: 170, fontWeight: 800, letterSpacing: -4, lineHeight: 1, opacity: Math.min(1, t * 1.4), scale: String(interpolate(t, [0, 1], [0.7, 1])), translate: `0 ${(1 - t) * 40}px`}}>Turnkeeper</div>
          <div style={{height: 10, width: 300, margin: '30px auto', borderRadius: 5, background: `linear-gradient(90deg, ${brand.blue}, ${brand.accent})`, scale: `${bar} 1`}} />
          <Rise t0={0.35} style={{fontSize: 46, fontWeight: 400, color: brand.mute}}>An AI treasurer for rotating savings circles, on PayPal sandbox rails</Rise>
          <div style={{display: 'flex', gap: 18, justifyContent: 'center', alignItems: 'center', marginTop: 54}}>
            {steps.map((s, i) => (
              <StepChip key={s} t0={0.8 + i * 0.18} text={s} arrow={i < steps.length - 1} />
            ))}
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
const StepChip: React.FC<{t0: number; text: string; arrow: boolean}> = ({t0, text, arrow}) => {
  const p = useReveal(t0, 18);
  return (
    <>
      <div style={{opacity: p, scale: String(0.85 + 0.15 * p), fontSize: 36, fontWeight: 600, padding: '12px 28px', borderRadius: 999, background: 'rgba(0,112,224,0.22)', border: `2px solid ${brand.blueSoft}`}}>{text}</div>
      {arrow ? <div style={{opacity: p, color: brand.accent, fontSize: 40}}>→</div> : null}
    </>
  );
};

/* ---------- s02 problem ---------- */
const CountUp: React.FC<{t0: number; to: number; suffix: string; dur?: number; decimals?: number}> = ({t0, to, suffix, dur = 1.1, decimals = 0}) => {
  const t = useSceneT();
  const p = interpolate(t, [t0, t0 + dur], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(0.16, 1, 0.3, 1)});
  return <>{(to * p).toFixed(decimals) + suffix}</>;
};

export const ProblemSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const l2 = sc.lines.find((l) => l.id === 'L02')!;
  const l3 = sc.lines.find((l) => l.id === 'L03')!;
  const ov = sc.overlays ?? [];
  const lt = ov.find((o) => o.type === 'lower_third');
  const srcs = ov.filter((o) => o.type === 'source_caption');
  const tStat1 = l3.at + (srcs[0]?.offset ?? 0);
  const tStat2 = l3.at + (srcs[1]?.offset ?? 3.2);
  return (
    <AbsoluteFill>
      <Backdrop />
      <H1>Someone is always the bank</H1>
      <PersonaCard t0={l2.at} />
      <StatCard t0={tStat1} x={690} big={<CountUp t0={tStat1} to={19} suffix="M" />} text="U.S. households were underbanked in 2023" tag="FDIC · 14.2% of households" />
      <StatCard t0={tStat2} x={1290} big={<CountUp t0={tStat2} to={28} suffix="%" />} text="of adults in Sub-Saharan Africa saved through a savings club (2024 data)" tag="World Bank Global Findex 2025 · 28.1%" />
      {lt ? (
        <Sequence from={tIn + Math.round(l2.at * fps)} durationInFrames={Math.round((l3.at - l2.at) * fps) + 6} layout="none">
          <LowerThird kicker="Persona" text={lt.text} frames={Math.round((l3.at - l2.at) * fps) + 6} />
        </Sequence>
      ) : null}
      {srcs.map((o, i) => {
        const start = l3.at + (o.offset ?? 0);
        const end = i + 1 < srcs.length ? l3.at + (srcs[i + 1].offset ?? 0) : sc.duration - 0.1;
        const frames = Math.round((end - start) * fps);
        return (
          <Sequence key={i} from={tIn + Math.round(start * fps)} durationInFrames={frames} layout="none">
            <LowerThird text={o.text} frames={frames} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

const PersonaCard: React.FC<{t0: number}> = ({t0}) => {
  const p = useReveal(t0 - 0.1);
  const facts = ['Organizer of a temple chit circle', '6 members · $200 a month · one pot per round', 'Run by hand today'];
  return (
    <div style={{position: 'absolute', left: 120, top: 250, width: 520, height: 460, boxSizing: 'border-box', borderRadius: 26, background: 'rgba(255,255,255,0.06)', border: '1.5px solid rgba(255,255,255,0.16)', padding: 34, opacity: p, translate: `${(1 - p) * -60}px 0`, fontFamily: brand.font, color: brand.white}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 24}}>
        <div style={{width: 96, height: 96, borderRadius: 48, background: `linear-gradient(135deg, ${brand.blue}, ${brand.accent})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 52, fontWeight: 800}}>P</div>
        <div>
          <div style={{fontSize: 52, fontWeight: 800}}>Priya</div>
          <div style={{fontSize: 22, color: brand.mute, fontStyle: 'italic'}}>composite persona, not a real person</div>
        </div>
      </div>
      <div style={{marginTop: 30, display: 'flex', flexDirection: 'column', gap: 16}}>
        {facts.map((f, i) => (
          <Rise key={i} t0={t0 + 0.4 + i * 0.35} style={{display: 'flex', gap: 14, fontSize: 28, lineHeight: 1.25}}>
            <div style={{width: 12, height: 12, borderRadius: 6, background: brand.accent, marginTop: 11, flexShrink: 0}} />
            <div>{f}</div>
          </Rise>
        ))}
      </div>
    </div>
  );
};

const StatCard: React.FC<{t0: number; x: number; big: React.ReactNode; text: string; tag: string}> = ({t0, x, big, text, tag}) => {
  const p = useReveal(t0 - 0.05, 16);
  return (
    <div style={{position: 'absolute', left: x, top: 250, width: 540, height: 460, boxSizing: 'border-box', borderRadius: 26, background: 'rgba(0,112,224,0.16)', border: `2px solid ${brand.blueSoft}`, padding: 36, opacity: p, translate: `0 ${(1 - p) * 60}px`, fontFamily: brand.font, color: brand.white}}>
      <div style={{fontSize: 150, fontWeight: 800, letterSpacing: -4, color: brand.accent, lineHeight: 1}}>{big}</div>
      <div style={{fontSize: 36, fontWeight: 600, marginTop: 18, lineHeight: 1.25}}>{text}</div>
      <div style={{fontSize: 22, color: brand.mute, marginTop: 20}}>{tag}</div>
    </div>
  );
};

/* ---------- s14 architecture ---------- */
export const ArchSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const nodes = [
    {label: 'Model', sub: 'proposes remedies, words nudges', t: wordAt(sc, 'L16', 'proposes')},
    {label: 'Policy engine', sub: 'deterministic · every money action passes it', t: wordAt(sc, 'L16', 'decides'), hi: true},
    {label: 'Humans', sub: 'consent once · vote · organizer’s one tap', t: wordAt(sc, 'L16', 'approve')},
    {label: 'PayPal sandbox', sub: 'one FIFO gateway · cached OAuth · PayPal-Request-Id per run', t: wordAt(sc, 'L16', 'matters') - 0.1},
  ];
  return (
    <AbsoluteFill>
      <Backdrop />
      <H1 size={64}>The model proposes. Code decides. People approve.</H1>
      <div style={{position: 'absolute', left: 120, top: 300, display: 'flex', alignItems: 'center'}}>
        {nodes.map((n, i) => (
          <React.Fragment key={n.label}>
            <ArchNode {...n} />
            {i < nodes.length - 1 ? <Edge t0={nodes[i + 1].t - 0.15} /> : null}
          </React.Fragment>
        ))}
      </div>
      <Rise t0={nodes[3].t + 0.5} style={{position: 'absolute', left: 120, top: 700, fontFamily: brand.font, fontSize: 30, color: brand.mute}}>
        One frozen charter (SHA-256) drives consent, remedies and the payout mandate.
      </Rise>
    </AbsoluteFill>
  );
};
const ArchNode: React.FC<{label: string; sub: string; t: number; hi?: boolean}> = ({label, sub, t, hi}) => {
  const p = useReveal(t - 0.1, 16);
  return (
    <div style={{width: 340, height: 300, borderRadius: 26, padding: 30, boxSizing: 'border-box', background: hi ? 'rgba(245,166,35,0.16)' : 'rgba(255,255,255,0.07)', border: `${hi ? 3 : 1.5}px solid ${hi ? brand.accent : 'rgba(255,255,255,0.2)'}`, opacity: p, scale: String(0.9 + 0.1 * p), boxShadow: hi ? '0 0 46px rgba(245,166,35,.35)' : 'none', fontFamily: brand.font, color: brand.white}}>
      <div style={{fontSize: 46, fontWeight: 800, lineHeight: 1.1}}>{label}</div>
      <div style={{fontSize: 26, color: brand.mute, marginTop: 16, lineHeight: 1.3}}>{sub}</div>
    </div>
  );
};
const Edge: React.FC<{t0: number}> = ({t0}) => {
  const p = useReveal(t0);
  return (
    <svg width={70} height={40} style={{opacity: p}}>
      <line x1={4} y1={20} x2={4 + 48 * p} y2={20} stroke={brand.accent} strokeWidth={5} strokeLinecap="round" />
      <polygon points="52,8 68,20 52,32" fill={brand.accent} opacity={p} />
    </svg>
  );
};

/* ---------- s15 APIs ---------- */
export const ApisSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const rows = [
    {k: 'Vault v3', v: 'Setup token with a billing plan: each member approves once', w: 'Vault'},
    {k: 'Orders v2', v: 'Merchant-initiated capture on the vaulted account, every round', w: 'Orders'},
    {k: 'Payouts', v: 'The pot to the round’s recipient, under the organizer’s mandate', w: 'Payouts'},
    {k: 'Invoicing via Agent Toolkit', v: 'create_invoice → send_invoice → generate_invoice_qr_code; get_order_details confirms captures', w: 'Invoicing'},
    {k: 'Webhooks', v: 'Signature verification, event dedupe, reconciliation', w: 'Webhooks'},
    {k: 'Balances (Reporting)', v: 'Shown as context with its refresh time; the circle’s ledger is the payout gate', w: 'Balances'},
  ];
  return (
    <AbsoluteFill>
      <Backdrop />
      <H1 size={62} top={70}>PayPal on the money path</H1>
      <div style={{position: 'absolute', left: 120, right: 120, top: 180, display: 'flex', flexDirection: 'column', gap: 12}}>
        {rows.map((r) => (
          <ApiRow key={r.k} k={r.k} v={r.v} t={wordAt(sc, 'L17', r.w) - 0.1} />
        ))}
      </div>
      <Rise t0={wordAt(sc, 'L17', 'Balances') + 0.8} style={{position: 'absolute', left: 120, right: 120, top: 806, fontFamily: brand.font, fontSize: 23, color: brand.mute, lineHeight: 1.3}}>
        4 Agent Toolkit tools on the demo path; Vault, Orders create, Payouts, Balances and Webhooks are direct REST. Real webhook delivery is not tested yet (unit-tested with mocks).
      </Rise>
    </AbsoluteFill>
  );
};
const ApiRow: React.FC<{k: string; v: string; t: number}> = ({k, v, t}) => {
  const p = useReveal(t, 18);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: 28, height: 84, borderRadius: 18, padding: '0 28px', background: 'rgba(255,255,255,0.06)', borderLeft: `8px solid ${brand.blue}`, opacity: p, translate: `${(1 - p) * 80}px 0`, fontFamily: brand.font, color: brand.white}}>
      <div style={{width: 400, fontSize: 32, fontWeight: 800, flexShrink: 0}}>{k}</div>
      <div style={{fontSize: 26, color: '#d5e1f2', lineHeight: 1.25}}>{v}</div>
    </div>
  );
};

/* ---------- s16 AI layer ---------- */
export const AiLayerSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const tMcp = wordAt(sc, 'L18', 'MCP');
  const tFree = wordAt(sc, 'L18', 'free');
  const tReplay = wordAt(sc, 'L18', 'replay');
  const tRules = wordAt(sc, 'L18', 'rules');
  const mcp = ['Internal MCP server: 18 tools; money tools policy-gated', 'Treasurer agent: at most 25 tool calls per task, per-task allowlists', 'No agent task can execute remedies', 'Public read-only MCP: 4 tools, demo circles only, PII masked'];
  const modes = [
    {chip: 'Free model', text: 'Gemini or Groq by default; Anthropic optional', t: tFree},
    {chip: 'AI replay', text: 'recorded model turns replayed, no quota spent', t: tReplay},
    {chip: 'Rules mode', text: 'deterministic fallback, same tools, no model', t: tRules},
  ];
  return (
    <AbsoluteFill>
      <Backdrop />
      <H1 size={62} top={80}>Tools behind MCP. A model is optional.</H1>
      <Panel x={120} t0={tMcp - 0.15} heading="MCP">
        {mcp.map((m, i) => (
          <Rise key={i} t0={tMcp + 0.2 + i * 0.3} style={{display: 'flex', gap: 14, fontSize: 28, lineHeight: 1.28}}>
            <div style={{width: 12, height: 12, borderRadius: 6, background: brand.accent, marginTop: 12, flexShrink: 0}} />
            <div>{m}</div>
          </Rise>
        ))}
      </Panel>
      <Panel x={1000} t0={tFree - 0.15} heading="Three modes, always labelled">
        {modes.map((m, i) => (
          <Rise key={i} t0={m.t - 0.1} style={{display: 'flex', gap: 16, alignItems: 'flex-start', fontSize: 28, lineHeight: 1.28}}>
            <div style={{fontSize: 22, fontWeight: 800, color: brand.navy, background: i === 2 ? brand.accent : brand.blueSoft, borderRadius: 999, padding: '4px 16px', whiteSpace: 'nowrap', marginTop: 2}}>{m.chip}</div>
            <div>{m.text}</div>
          </Rise>
        ))}
      </Panel>
      <Rise t0={tRules + 0.9} style={{position: 'absolute', left: 120, top: 770, fontFamily: brand.font, fontSize: 28, color: brand.accent, fontWeight: 600}}>
        In the recorded story the free model timed out, so every step ran in labelled rules mode.
      </Rise>
    </AbsoluteFill>
  );
};
const Panel: React.FC<{x: number; t0: number; heading: string; children: React.ReactNode}> = ({x, t0, heading, children}) => {
  const p = useReveal(t0, 20);
  return (
    <div style={{position: 'absolute', left: x, top: 230, width: 800, height: 500, borderRadius: 26, padding: '30px 38px', boxSizing: 'border-box', background: 'rgba(255,255,255,0.06)', border: '1.5px solid rgba(255,255,255,0.18)', opacity: p, translate: `0 ${(1 - p) * 50}px`, fontFamily: brand.font, color: brand.white}}>
      <div style={{fontSize: 40, fontWeight: 800, color: brand.blueSoft, marginBottom: 20}}>{heading}</div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 20}}>{children}</div>
    </div>
  );
};

/* ---------- s17 evidence ---------- */
const BigStat: React.FC<{t0: number; big: React.ReactNode; text: string; hi?: boolean}> = ({t0, big, text, hi}) => {
  const p = useReveal(t0, 18);
  return (
    <div style={{flex: 1, borderRadius: 24, padding: '28px 30px', background: hi ? 'rgba(245,166,35,0.16)' : 'rgba(0,112,224,0.16)', border: `2px solid ${hi ? brand.accent : brand.blueSoft}`, opacity: p, translate: `0 ${(1 - p) * 50}px`, fontFamily: brand.font, color: brand.white, boxShadow: hi ? '0 0 40px rgba(245,166,35,.3)' : 'none'}}>
      <div style={{fontSize: 108, fontWeight: 800, letterSpacing: -3, lineHeight: 1, color: hi ? brand.accent : brand.white}}>{big}</div>
      <div style={{fontSize: 28, marginTop: 14, lineHeight: 1.25, color: '#dbe6f6'}}>{text}</div>
    </div>
  );
};

export const EvidenceSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const t = useSceneT();
  const tA = lineAt(sc, 'L19');
  const tB = lineAt(sc, 'L20') - 0.2;
  const swap = interpolate(t, [tB - 0.35, tB + 0.15], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(0.45, 0, 0.15, 1)});
  const fA = Math.round((tB - tA - 0.2) * fps);
  const fB = Math.round((sc.duration - tB - 0.1) * fps);
  return (
    <AbsoluteFill>
      <Backdrop />
      <H1 size={62} top={70}>Evidence</H1>
      <div style={{position: 'absolute', inset: 0, opacity: 1 - swap, translate: `${-swap * 120}px 0`}}>
        <Rise t0={tA} style={{position: 'absolute', left: 120, top: 170, fontFamily: brand.font, fontSize: 34, fontWeight: 600, color: brand.blueSoft}}>Remedy eval · rules mode · 20 scenarios × 5 runs</Rise>
        <div style={{position: 'absolute', left: 120, right: 120, top: 240, display: 'flex', gap: 26}}>
          <BigStat t0={wordAt(sc, 'L19', 'ninety') - 0.1} big={<CountUp t0={wordAt(sc, 'L19', 'ninety') - 0.1} to={0.95} suffix="" decimals={2} />} text="charter-legal before the policy check" />
          <BigStat t0={wordAt(sc, 'L19', 'zero') - 0.1} big="0" text="illegal actions executed" hi />
          <BigStat t0={wordAt(sc, 'L19', 'zero') + 1.0} big={<CountUp t0={wordAt(sc, 'L19', 'zero') + 1.0} to={1} suffix=".0" decimals={0} />} text="commitment (date and amount) exact match" />
        </div>
        <Rise t0={wordAt(sc, 'L19', 'zero') + 1.4} style={{position: 'absolute', left: 120, right: 120, top: 560, fontFamily: brand.font, fontSize: 26, lineHeight: 1.35, color: brand.mute}}>
          The one miss is blocked by the gates and goes to the organizer. Live free model (gemini-3.5-flash-lite, n = 37): 37/37 legal, 0 illegal, but it matched the preferred remedy in only 3 of 37 runs.
        </Rise>
        <Sequence from={tIn + Math.round((tA + 0.3) * fps)} durationInFrames={Math.max(1, fA)} layout="none">
          <LowerThird text="eval/RESULTS.md · synthetic scenarios, rules mode (n = 100 runs)" frames={Math.max(1, fA)} bottom={232} />
        </Sequence>
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: swap, translate: `${(1 - swap) * 120}px 0`}}>
        <Rise t0={tB + 0.2} style={{position: 'absolute', left: 120, top: 170, fontFamily: brand.font, fontSize: 34, fontWeight: 600, color: brand.blueSoft}}>Full Judge Mode story · real PayPal sandbox · AI in rules mode</Rise>
        <div style={{position: 'absolute', left: 120, right: 120, top: 240, display: 'flex', gap: 26}}>
          <BigStat t0={tB + 0.4} big="11 / 11" text="story steps passed live on the PayPal sandbox" />
          <BigStat t0={tB + 0.8} big="5 + 1" text="round 3: five captures confirmed via the toolkit, Ravi’s decline sandbox-injected" hi />
          <BigStat t0={tB + 1.2} big="2" text={`payouts: round 3 and clean round 4, ${captured.receipt3.amount} each`} />
        </div>
        <Rise t0={tB + 1.8} style={{position: 'absolute', left: 120, right: 120, top: 560, fontFamily: brand.font, fontSize: 26, lineHeight: 1.35, color: brand.mute}}>
          Round 3’s payout item later read SUCCESS ($0.25 fee); round 4 was still PENDING when captured. Forecaster backtest (synthetic, n = 60): precision 0.647, recall 0.647.
        </Rise>
        <Sequence from={tIn + Math.round((tB + 0.4) * fps)} durationInFrames={Math.max(1, fB)} layout="none">
          <LowerThird text="capture-log.json (this recording) · docs/live-run/ · eval/RESULTS.md (backtest, synthetic)" frames={Math.max(1, fB)} bottom={232} />
        </Sequence>
      </div>
      <Rise t0={tA + 6} style={{position: 'absolute', left: 120, right: 120, top: 720, fontFamily: brand.font, fontSize: 24, color: brand.mute}}>
        711 pytest + 90 vitest tests; PayPal and every model are mocked in tests.
      </Rise>
    </AbsoluteFill>
  );
};

/* ---------- s18 prior art + honesty ---------- */
export const PriorArtSlide: React.FC<{sc: SbScene}> = ({sc}) => {
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const t = useSceneT();
  const tB = lineAt(sc, 'L22') - 0.25;
  const swap = interpolate(t, [tB - 0.35, tB + 0.15], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(0.45, 0, 0.15, 1)});
  const rows = [
    {name: 'PayPal money pooling', what: 'collects for one group purchase', src: 'PayPal newsroom, 14 Nov 2024'},
    {name: 'Lending-circle nonprofits (MAF)', what: 'formal circles, run by a nonprofit', src: 'missionassetfund.org'},
    {name: 'ROSCA apps (MoneyFellows)', what: 'the circle as an app, on its own rails', src: 'TechCrunch, 2025'},
  ];
  const items = [
    {text: 'Sandbox only: the app refuses any PAYPAL_ENV other than sandbox', w: 'sandbox'},
    {text: 'Fees shown before consent: 3.49% + $0.49 per charge (≈ $7.47 per $200), $0.25 per payout', w: 'Fees'},
    {text: 'Buffer optional: organizer-funded, disclosed, used only through a voted remedy', w: 'buffer'},
    {text: 'No credit claims: nothing is reported to credit bureaus', w: 'credit'},
  ];
  const tFees = wordAt(sc, 'L22', 'Fees');
  const fFees = Math.round((sc.duration - tFees - 0.1) * fps);
  return (
    <AbsoluteFill>
      <Backdrop />
      <div style={{position: 'absolute', inset: 0, opacity: 1 - swap, translate: `${-swap * 120}px 0`}}>
        <H1 size={62} top={80}>Prior art</H1>
        <div style={{position: 'absolute', left: 120, right: 120, top: 210, display: 'flex', flexDirection: 'column', gap: 16}}>
          {rows.map((r, i) => (
            <Rise key={r.name} t0={lineAt(sc, 'L21') + 0.1 + i * 0.45} style={{display: 'flex', alignItems: 'center', gap: 28, padding: '18px 28px', borderRadius: 18, background: 'rgba(255,255,255,0.06)', borderLeft: `8px solid ${i === 0 ? brand.accent : brand.blue}`, fontFamily: brand.font, color: brand.white}}>
              <div style={{width: 560, fontSize: 32, fontWeight: 800}}>{r.name}</div>
              <div style={{flex: 1, fontSize: 28, color: '#d5e1f2'}}>{r.what}</div>
              <div style={{fontSize: 21, color: brand.mute, textAlign: 'right', width: 280}}>{r.src}</div>
            </Rise>
          ))}
        </div>
        <Rise t0={wordAt(sc, 'L21', 'rotates') - 0.6} style={{position: 'absolute', left: 120, right: 120, top: 610, fontFamily: brand.font, fontSize: 36, fontWeight: 600, color: brand.accent, lineHeight: 1.3}}>
          Turnkeeper is the pool that rotates: consent once, collect every round, recover shortfalls, pay a different member each time.
        </Rise>
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: swap, translate: `${(1 - swap) * 120}px 0`}}>
        <H1 size={62} top={80} t0={tB - 0.1}>What we do not claim</H1>
        <div style={{position: 'absolute', left: 120, right: 120, top: 210, display: 'flex', flexDirection: 'column', gap: 16}}>
          {items.map((it, i) => (
            <Rise key={i} t0={wordAt(sc, 'L22', it.w) - 0.1} style={{display: 'flex', alignItems: 'center', gap: 22, padding: '16px 28px', borderRadius: 18, background: 'rgba(255,255,255,0.06)', fontFamily: brand.font, color: brand.white, fontSize: 31, lineHeight: 1.25}}>
              <div style={{width: 40, height: 40, borderRadius: 20, background: 'rgba(55,196,123,0.2)', border: `2px solid ${brand.good}`, color: brand.good, fontSize: 24, fontWeight: 800, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0}}>✓</div>
              <div>{it.text}</div>
            </Rise>
          ))}
        </div>
        <Rise t0={wordAt(sc, 'L22', 'credit') + 1.1} style={{position: 'absolute', left: 120, right: 120, top: 660, fontFamily: brand.font, fontSize: 24, color: brand.mute}}>
          Production would need PayPal’s Payouts approval. PayPal wallet only; Venmo cut.
        </Rise>
        <Sequence from={tIn + Math.round(tFees * fps)} durationInFrames={Math.max(1, fFees)} layout="none">
          <LowerThird text="PayPal merchant fees page, updated 1 Oct 2026 · docs/sources.md" frames={Math.max(1, fFees)} bottom={232} />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
};

/* ---------- s19 outro ---------- */
export const OutroSlide: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const f = Math.max(0, frame - tIn);
  const t = spring({frame: f - 4, fps, config: {damping: 15, stiffness: 120, mass: 0.9}});
  return (
    <AbsoluteFill>
      <Backdrop />
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily: brand.font, color: brand.white}}>
        <div style={{textAlign: 'center'}}>
          <div style={{fontSize: 150, fontWeight: 800, letterSpacing: -3, opacity: Math.min(1, t * 1.4), scale: String(interpolate(t, [0, 1], [0.75, 1]))}}>Turnkeeper</div>
          <Rise t0={0.35} style={{fontSize: 60, fontWeight: 600, color: brand.accent, marginTop: 10}}>Your circle, kept.</Rise>
          <Rise t0={0.9} style={{marginTop: 54, display: 'inline-block', fontSize: 44, fontWeight: 600, fontFamily: brand.mono, padding: '14px 36px', borderRadius: 18, background: 'rgba(0,112,224,0.22)', border: `2px solid ${brand.blueSoft}`}}>
            github.com/ArnabZ-77/turnkeeper
          </Rise>
          <Rise t0={1.3} style={{marginTop: 30, fontSize: 28, color: brand.mute}}>Try Judge Mode in the repo: 11 steps, one button</Rise>
          <Rise t0={1.6} style={{marginTop: 18, fontSize: 24, color: brand.mute}}>PayPal AI Hackathon 2026 · PayPal sandbox · MIT license</Rise>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
