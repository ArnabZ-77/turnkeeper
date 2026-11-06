import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useVideoConfig} from 'remotion';
import {brand} from './brand';
import {FootageLayer} from './components/Footage';
import {Backdrop, Icon, useReveal} from './components/Slides';
import {wordAt, type SbScene} from './film-data';
import {useSceneT} from './scene-context';
import type {Rect, ZoomKeyframe} from './types';

const ease = Easing.bezier(0.16, 1, 0.3, 1);
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const span = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {...clamp, easing: ease});

/** The six members of the demo circle, in payout order (app/fixtures/demo_circle.json). */
const MEMBERS = [
  {h: 'meena', n: 'Meena'},
  {h: 'asha', n: 'Asha'},
  {h: 'dev', n: 'Dev'},
  {h: 'lin', n: 'Lin'},
  {h: 'ravi', n: 'Ravi'},
  {h: 'tomas', n: 'Tomas'},
];

const angle = (i: number, spin: number) => ((i * 60 - 90 + spin) * Math.PI) / 180;

/** Ring of member dots; `spin` in degrees. Used by the title emblem and the circle explainer. */
const MemberRing: React.FC<{cx: number; cy: number; r: number; spin: number; dot: number; labels?: boolean; glow?: string}> = ({cx, cy, r, spin, dot, labels, glow}) => (
  <>
    <div style={{position: 'absolute', left: cx - r, top: cy - r, width: r * 2, height: r * 2, borderRadius: '50%', border: '2px dashed rgba(90,168,245,0.35)'}} />
    {MEMBERS.map((m, i) => {
      const a = angle(i, spin);
      const x = cx + r * Math.cos(a);
      const y = cy + r * Math.sin(a);
      const hot = glow === m.h;
      return (
        <div key={m.h} style={{position: 'absolute', left: x - dot / 2, top: y - dot / 2, width: dot, height: dot}}>
          <div
            style={{
              width: dot,
              height: dot,
              borderRadius: '50%',
              background: hot ? `linear-gradient(135deg, ${brand.accent}, #ffcf6b)` : `linear-gradient(135deg, ${brand.blue}, ${brand.blueSoft})`,
              border: '3px solid rgba(255,255,255,0.85)',
              boxShadow: hot ? `0 0 ${dot * 0.6}px ${brand.accent}` : '0 10px 30px rgba(0,0,0,.35)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: brand.font,
              fontWeight: 800,
              fontSize: dot * 0.42,
              color: hot ? brand.navy : brand.white,
            }}
          >
            {m.n[0]}
          </div>
          {labels ? (
            <div style={{position: 'absolute', left: -60, right: -60, top: dot + 8, textAlign: 'center', fontFamily: brand.font, fontSize: 26, fontWeight: 700, color: hot ? brand.accent : brand.white}}>{m.n}</div>
          ) : null}
        </div>
      );
    })}
  </>
);

const Pot: React.FC<{x: number; y: number; size: number; fill: number; scale: number; label?: boolean}> = ({x, y, size, fill, scale, label}) => (
  <div
    style={{
      position: 'absolute',
      left: x - size / 2,
      top: y - size / 2,
      width: size,
      height: size,
      borderRadius: '50%',
      background: `radial-gradient(circle at 35% 30%, #ffd98a, ${brand.accent} 60%, #c67d00)`,
      boxShadow: `0 0 ${40 + 40 * fill}px rgba(245,166,35,${0.25 + 0.35 * fill})`,
      scale: String(scale),
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      fontFamily: brand.font,
      color: brand.navy,
    }}
  >
    {label ? (
      <>
        <div style={{fontSize: 22, fontWeight: 800, letterSpacing: 1}}>POT</div>
        <div style={{fontSize: 46, fontWeight: 900, lineHeight: 1}}>{`$${Math.round(1155 * fill).toLocaleString('en-US')}`}</div>
      </>
    ) : null}
  </div>
);

/* ---------- 0:00 cold open: the alert, the board flips, the title ---------- */

const R = (x: number, y: number, w: number): Rect => ({x, y, w, h: (w * 9) / 16});
const K = (t: number, r: Rect): ZoomKeyframe => ({t, ...r});
const V2 = (name: string, from: number, to: number) => ({src: `footage/v2/${name}-s.mp4`, from, to, rate: 1});
const BOARD = {w: 1920, h: 2300, k: 1.5};

export const ColdOpen: React.FC<{sc: SbScene}> = ({sc}) => {
  const t = useSceneT();
  const {fps} = useVideoConfig();
  const tPriya = wordAt(sc, 'C01', 'Priya');
  const tHere = wordAt(sc, 'C01', 'Here');
  const tChase = wordAt(sc, 'C01', 'chase');

  // phase 1: the decline alert drops in and shakes once
  const drop = t < 0.15 ? 0 : spring({frame: Math.round((t - 0.15) * fps), fps, config: {damping: 13, mass: 0.8}});
  const shake = t > 0.75 && t < 1.15 ? Math.sin((t - 0.75) * 60) * 9 * (1 - (t - 0.75) / 0.4) : 0;
  const alertOut = span(t, tPriya - 0.35, tPriya + 0.05);

  // phase 2: Ravi's row on the live board, Declined then Covered
  const boardIn = span(t, tPriya - 0.3, tPriya + 0.15);
  const boardOut = span(t, tHere - 0.35, tHere);
  const covered = t >= tChase + 0.15;

  // phase 3: the title
  const title = span(t, tHere - 0.15, tHere + 0.6);
  const spin = t * 22;

  const cam = [K(0, R(330, 400, 600)), K(2, R(330, 400, 600))];
  return (
    <AbsoluteFill>
      <Backdrop />

      {/* phase 2 board (under the alert so the cut is a dissolve) */}
      <div style={{position: 'absolute', inset: 0, opacity: boardIn * (1 - boardOut)}}>
        <FootageLayer
          segs={[V2('v2-07-round3', 12.6, 12.6 + 2.6), V2('v2-11-execute', 24.6, 26.8)]}
          zoom={cam}
          src={BOARD}
          win={{x: 260, y: 150, w: 1400, h: 788}}
          t0={tPriya - 0.3}
          t1={tHere}
          rounded={22}
          badge={false}
        />
        <div
          style={{
            position: 'absolute',
            left: 300,
            top: 860,
            padding: '12px 26px',
            borderRadius: 999,
            fontFamily: brand.font,
            fontSize: 34,
            fontWeight: 800,
            color: brand.navy,
            background: covered ? brand.accent : brand.bad,
            boxShadow: '0 16px 40px rgba(0,0,0,.4)',
          }}
        >
          {covered ? 'Round 3: covered by the circle buffer' : "Ravi's $200: declined"}
        </div>
      </div>

      {/* phase 1 alert */}
      <div style={{position: 'absolute', inset: 0, opacity: 1 - alertOut, display: 'flex', justifyContent: 'center'}}>
        <div
          style={{
            marginTop: interpolate(drop, [0, 1], [-260, 300]),
            translate: `${shake}px 0`,
            width: 900,
            height: 210,
            borderRadius: 34,
            background: 'rgba(255,255,255,0.97)',
            boxShadow: '0 50px 120px rgba(0,0,0,.55)',
            display: 'flex',
            alignItems: 'center',
            gap: 34,
            padding: '0 44px',
            boxSizing: 'border-box',
            fontFamily: brand.font,
          }}
        >
          <div style={{width: 112, height: 112, borderRadius: 56, background: brand.bad, color: brand.white, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 76, fontWeight: 900, flexShrink: 0}}>!</div>
          <div style={{color: brand.ink}}>
            <div style={{fontSize: 26, fontWeight: 700, color: '#5f6b7a', letterSpacing: 0.5}}>PAYPAL · ROUND 3 CONTRIBUTION</div>
            <div style={{fontSize: 52, fontWeight: 800, marginTop: 4}}>Payment declined</div>
            <div style={{fontSize: 30, fontWeight: 600, color: '#3d4a5c', marginTop: 4}}>Ravi Sharma · $200.00 · sandbox</div>
          </div>
        </div>
      </div>

      {/* phase 3 title: a small ring of the six members around the pot, then the wordmark */}
      <div style={{position: 'absolute', inset: 0, opacity: title}}>
        <MemberRing cx={960} cy={300} r={96 + 24 * title} spin={spin} dot={40} />
        <Pot x={960} y={300} size={88} fill={1} scale={0.6 + 0.4 * title} />
        <div style={{position: 'absolute', left: 0, right: 0, top: 470, textAlign: 'center', fontFamily: brand.font, color: brand.white, scale: String(0.92 + 0.08 * title)}}>
          <div style={{fontSize: 136, fontWeight: 800, letterSpacing: -4, lineHeight: 1}}>Turnkeeper</div>
        </div>
        <div style={{position: 'absolute', left: 0, right: 0, top: 640, textAlign: 'center', fontFamily: brand.font, fontSize: 46, fontWeight: 600, color: brand.accent, opacity: span(t, tHere + 0.3, tHere + 0.9)}}>
          An AI treasurer for savings circles, on PayPal
        </div>
        <div style={{position: 'absolute', left: 0, right: 0, top: 720, textAlign: 'center', fontFamily: brand.font, fontSize: 26, fontWeight: 600, color: brand.mute, opacity: span(t, tHere + 0.6, tHere + 1.2)}}>
          PayPal AI Hackathon 2026 · runs on the PayPal sandbox
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ---------- 0:06 what a savings circle is, and who carries it ---------- */

const ROLES: {w: string; kind: 'bank' | 'ledger' | 'shield'; label: string}[] = [
  {w: 'bank', kind: 'bank', label: 'the bank'},
  {w: 'collector', kind: 'ledger', label: 'the collector'},
  {w: 'insurer', kind: 'shield', label: 'the insurer'},
];

const Stat: React.FC<{t0: number; top: number; big: string; to: number; text: string; tag: string}> = ({t0, top, big, to, text, tag}) => {
  const t = useSceneT();
  const p = useReveal(t0 - 0.05, 16);
  const n = Math.round(to * span(t, t0, t0 + 0.9));
  return (
    <div style={{position: 'absolute', left: 1080, top, width: 720, height: 300, boxSizing: 'border-box', borderRadius: 26, background: 'rgba(0,112,224,0.16)', border: `2px solid ${brand.blueSoft}`, padding: '28px 34px', opacity: p, translate: `${(1 - p) * 80}px 0`, fontFamily: brand.font, color: brand.white, display: 'flex', gap: 30, alignItems: 'center'}}>
      <div style={{fontSize: 118, fontWeight: 800, letterSpacing: -4, color: brand.accent, lineHeight: 1, minWidth: 290}}>{`${n}${big}`}</div>
      <div>
        <div style={{fontSize: 32, fontWeight: 600, lineHeight: 1.25}}>{text}</div>
        <div style={{fontSize: 22, color: brand.mute, marginTop: 14, lineHeight: 1.25}}>{tag}</div>
      </div>
    </div>
  );
};

/** One of Priya's three jobs, revealed on its spoken word. */
const RoleChip: React.FC<{t0: number; kind: 'bank' | 'ledger' | 'shield'; label: string}> = ({t0, kind, label}) => {
  const p = useReveal(t0, 16);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: 26, opacity: p, translate: `${(1 - p) * 50}px 0`, padding: '16px 26px', borderRadius: 22, background: 'rgba(255,255,255,0.07)', border: '1.5px solid rgba(255,255,255,0.18)'}}>
      <Icon kind={kind} size={70} />
      <div style={{fontSize: 44, fontWeight: 800}}>{label}</div>
    </div>
  );
};

export const CircleIntro: React.FC<{sc: SbScene}> = ({sc}) => {
  const t = useSceneT();
  const tN = wordAt(sc, 'P02', 'Nineteen');
  const tS = wordAt(sc, 'P02', 'Sub');
  const roleT = ROLES.map((r) => wordAt(sc, 'P01', r.w));

  // the ring: contributions fly in, the pot fills, then goes to this round's recipient (Dev, round 3)
  const cx = 560;
  const cy = 560;
  const r = 250;
  const spin = 0;
  const payStart = 0.3;
  const stagger = 0.2;
  const filled = payStart + stagger * 5 + 0.55;
  const potFill = span(t, payStart, filled);
  const potGo = span(t, filled + 0.3, filled + 1.0);
  const devA = angle(MEMBERS.findIndex((m) => m.h === 'dev'), spin);
  const potX = cx + potGo * (r - 40) * Math.cos(devA);
  const potY = cy + potGo * (r - 40) * Math.sin(devA);

  const header = useReveal(0.05, 18);
  const priya = useReveal(0.15, 18);
  const roleOut = span(t, tN - 0.45, tN - 0.05);
  return (
    <AbsoluteFill>
      <Backdrop />
      <div style={{position: 'absolute', left: 120, right: 120, top: 64, fontFamily: brand.font, color: brand.white, opacity: header, translate: `0 ${(1 - header) * -20}px`}}>
        <div style={{fontSize: 50, fontWeight: 800, letterSpacing: -1}}>A savings circle: everyone pays in, one member takes the pot</div>
        <div style={{fontSize: 30, fontWeight: 600, color: brand.blueSoft, marginTop: 8}}>chit · susu · tanda · chama · stokvel</div>
      </div>

      <MemberRing cx={cx} cy={cy} r={r} spin={spin} dot={92} labels glow={potGo > 0.95 ? 'dev' : undefined} />
      {MEMBERS.map((m, i) => {
        const a = angle(i, spin);
        const s0 = payStart + i * stagger;
        const p = span(t, s0, s0 + 0.55);
        if (p <= 0 || p >= 1) return null;
        return (
          <div key={m.h} style={{position: 'absolute', left: cx + (1 - p) * r * Math.cos(a) - 46, top: cy + (1 - p) * r * Math.sin(a) - 20, width: 92, height: 40, borderRadius: 20, background: brand.accent, color: brand.navy, fontFamily: brand.font, fontWeight: 800, fontSize: 24, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 8px 20px rgba(0,0,0,.35)'}}>
            $200
          </div>
        );
      })}
      <Pot x={potX} y={potY} size={190} fill={potFill} scale={0.55 + 0.45 * potFill - 0.3 * potGo} label />
      <div style={{position: 'absolute', left: cx - 450, width: 900, top: cy + r + 100, textAlign: 'center', fontFamily: brand.font, fontSize: 28, fontWeight: 700, color: brand.white, opacity: span(t, filled + 0.8, filled + 1.2)}}>
        Round 3: the pot goes to Dev. Next month, to Lin.
      </div>

      {/* Priya and her three jobs, each on its word; then the two figures take her place */}
      <div style={{position: 'absolute', left: 1080, top: 250, width: 720, fontFamily: brand.font, color: brand.white, opacity: priya * (1 - roleOut), translate: `${roleOut * 80}px 0`}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 24}}>
          <div style={{width: 104, height: 104, borderRadius: 52, background: `linear-gradient(135deg, ${brand.blue}, ${brand.accent})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 56, fontWeight: 800}}>P</div>
          <div>
            <div style={{fontSize: 54, fontWeight: 800}}>Priya, the organizer</div>
            <div style={{fontSize: 22, color: brand.mute, fontStyle: 'italic'}}>composite persona, not a real person</div>
          </div>
        </div>
        <div style={{marginTop: 40, display: 'flex', flexDirection: 'column', gap: 22}}>
          {ROLES.map((role, i) => (
            <RoleChip key={role.w} t0={roleT[i] - 0.15} kind={role.kind} label={role.label} />
          ))}
        </div>
      </div>

      <Stat t0={tN - 0.1} top={250} big="M" to={19} text="U.S. households were underbanked in 2023" tag="FDIC National Survey · 14.2% of households" />
      <Stat t0={tS - 0.1} top={580} big="%" to={28} text="of adults in Sub-Saharan Africa saved through a savings club (2024 data)" tag="World Bank Global Findex 2025 · 28.1%" />
    </AbsoluteFill>
  );
};
