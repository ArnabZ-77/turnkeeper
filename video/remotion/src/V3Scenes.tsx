import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {brand, FPS} from './brand';
import {FootageLayer, type Win} from './components/Footage';
import {Backdrop, useReveal} from './components/Slides';
import {Footnote, HonestChip} from './components/Overlays';
import {wordAt, type SbScene} from './film-data';
import transcriptJson from './data/mcp_transcript.json';
import {useTIn} from './scene-context';

type Call = {tool: string; args: Record<string, string>; result: Record<string, any>};
const transcript = transcriptJson as unknown as {label: string; calls: Call[]};

const useT = () => {
  const frame = useCurrentFrame();
  const tIn = useTIn();
  return (frame - tIn) / FPS;
};

/** One terminal line that types itself in from `t0`. */
const Typed: React.FC<{t0: number; text: string; color?: string; cps?: number; bold?: boolean}> = ({t0, text, color = brand.white, cps = 70, bold}) => {
  const t = useT();
  if (t < t0) return null;
  const n = Math.min(text.length, Math.floor((t - t0) * cps));
  return <div style={{color, fontWeight: bold ? 700 : 400, whiteSpace: 'pre-wrap'}}>{text.slice(0, n)}</div>;
};

const short = (r: Record<string, any>) => {
  if (r.error) return `${r.error}: ${r.message}`;
  if (r.request) {
    const q = r.request;
    const swap = (q.remedies || []).find((x: any) => x.type === 'turn_swap');
    return `asks: ${q.item} for round ${q.round_n} · ${(q.remedies || []).map((x: any) => x.type).join(' + ')}${swap ? ` (with ${swap.swap_with})` : ''} · answers: ${q.choices.join(' / ')}`;
  }
  if (r.recorded) return `recorded: ${r.recorded} (${r.item}) · his mandate's rule: ${r.floor?.rule}`;
  if (r.invoice) return `invoice ${r.invoice.invoice_id} · $${r.invoice.amount} due ${r.invoice.due_date} · ${r.invoice.status} (via ${r.invoice.status_source})`;
  return JSON.stringify(r).slice(0, 120);
};

/** "Ravi's own assistant": the recorded public-MCP transcript, typed out (tokens redacted). */
export const AssistantTerminal: React.FC<{sc: SbScene}> = ({sc}) => {
  const calls = transcript.calls;
  const tTok = wordAt(sc, 'X01', 'token');
  const tTomas = wordAt(sc, 'X01', 'Tomas');
  const starts = [0.2, 1.4, tTomas - 1.9, tTomas + 0.2];
  const win = {x: 150, y: 222, w: 1620, h: 690};
  const reveal = useReveal(0, 18);
  return (
    <AbsoluteFill>
      <Backdrop />
      <div
        style={{
          position: 'absolute', left: win.x, top: win.y, width: win.w, height: win.h, borderRadius: 18,
          background: '#07111f', border: '2px solid #2b3a52', boxShadow: '0 40px 90px rgba(0,0,0,.5)', opacity: reveal,
          fontFamily: brand.mono, fontSize: 27, lineHeight: 1.45, padding: '64px 44px 30px', overflow: 'hidden',
        }}
      >
        <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 46, background: '#12233b', display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px'}}>
          {['#ff6b6b', '#f5a623', '#37c47b'].map((c) => (
            <span key={c} style={{width: 14, height: 14, borderRadius: 7, background: c}} />
          ))}
          <span style={{marginLeft: 16, fontFamily: brand.font, fontSize: 22, color: brand.mute}}>Ravi's own AI assistant · any MCP client → Turnkeeper /mcp</span>
        </div>
        {calls.map((c, i) => {
          const args = Object.entries(c.args)
            .filter(([, v]) => v !== '')
            .map(([k, v]) => `${k}=${k === 'cap' ? 'tkc1.…' : JSON.stringify(v)}`)
            .join(', ');
          const bad = Boolean(c.result?.error);
          return (
            <div key={i} style={{marginBottom: 14}}>
              <Typed t0={starts[i] ?? 0} text={`> ${c.tool}(${args})`} color={brand.blueSoft} cps={160} />
              <Typed t0={(starts[i] ?? 0) + 0.5} text={`  ${short(c.result || {})}`} color={bad ? brand.bad : brand.good} bold={bad} cps={200} />
            </div>
          );
        })}
      </div>
      <HonestChip text="Recorded transcript, tokens redacted" sub="One token: one member, one question, 15 minutes, single use" t0={0.4} dur={sc.duration - 0.8} left={150} top={24} good />
    </AbsoluteFill>
  );
};

/** Three members' passbooks, side by side: Ravi (Hindi), Tomas, Dev. Recorded from the live app. */
export const PassbookTriptych: React.FC<{sc: SbScene}> = ({sc}) => {
  const src = {w: 780, h: 1688};
  const w = 344;
  const h = Math.round((w * src.h) / src.w);
  const top = 160;
  const xs = [330, 788, 1246];
  const who = [
    {m: 'ravi', label: 'Ravi · हिंदी', delay: 0},
    {m: 'tomas', label: 'Tomas', delay: 0.45},
    {m: 'dev', label: 'Dev', delay: 0.9},
  ];
  const t = useT();
  const len = sc.duration + 0.5;
  return (
    <AbsoluteFill>
      <Backdrop />
      {who.map((p, i) => {
        const win: Win = {x: xs[i], y: top, w, h};
        const rise = interpolate(t, [p.delay, p.delay + 0.6], [60, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        const op = interpolate(t, [p.delay, p.delay + 0.4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        return (
          <div key={p.m} style={{position: 'absolute', inset: 0, translate: `0 ${rise}px`, opacity: op}}>
            <div style={{position: 'absolute', left: win.x - 14, top: win.y - 14, width: win.w + 28, height: win.h + 28, borderRadius: 46, background: 'linear-gradient(145deg, #2b3a52, #0a1424)', border: '2px solid #46597a', boxShadow: '0 30px 70px rgba(0,0,0,.5)'}} />
            <FootageLayer
              segs={[{src: `footage/v3/v3-pb-${p.m}-s.mp4`, from: 1.2, to: 1.2 + len, rate: 1}]}
              zoom={[{t: 0, x: 0, y: 0, w: src.w, h: src.h}]}
              src={src}
              win={win}
              rounded={32}
              badge={false}
            />
            <div style={{position: 'absolute', left: win.x, width: win.w, top: win.y + win.h + 18, textAlign: 'center', fontFamily: p.m === 'ravi' ? brand.fontDeva : brand.font, fontSize: 30, fontWeight: 700, color: brand.white}}>{p.label}</div>
          </div>
        );
      })}
      <Footnote text="Live app, sandbox. Rounds 1-2 are seeded history (marked replay); round 3 is live. Nobody else's shortfall is named." frames={Math.round(sc.duration * FPS)} top={60} left={300} />
    </AbsoluteFill>
  );
};
