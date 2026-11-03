import React from 'react';
import {AbsoluteFill, Sequence, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand, FPS} from './brand';
import {FootageLayer, StripTag, WIN, type Win} from './components/Footage';
import {PhoneBackdrop} from './components/Phone';
import {ReadbackCard} from './components/Cards';
import {Footnote, HonestChip, LowerThird} from './components/Overlays';
import {captured, lineAt, readback, wordAt, type SbScene} from './film-data';
import {useReveal} from './components/Slides';
import {useTIn} from './scene-context';
import type {Note, Rect, Seg, ZoomKeyframe} from './types';

/** v2 captures: 1920x2300 layout px at deviceScaleFactor 2 (3840x4600 files), so every panel is in view and sharp when zoomed. */
const D = {w: 1920, h: 2300, k: 1.5};
const R = (x: number, y: number, w: number): Rect => ({x, y, w, h: (w * 9) / 16});
const PR = (x: number, y: number, w: number): Rect => ({x, y, w, h: (w * 1688) / 780});
const FULL = R(0, 0, 1920);
const PFULL = PR(0, 0, 780);
const K = (t: number, r: Rect): ZoomKeyframe => ({t, ...r});

const V = (name: string) => `footage/v2/${name}-s.mp4`;
const seg = (name: string, from: number, to: number, rate = 1): Seg => ({src: V(name), from, to, rate});

/** All footage times below are file times (s) found with frame differencing on the 30 fps capture files. */
const P = {
  reset: 'v2-00-reset',
  mandates: 'v2-01c-mandates',
  compile: 'v2-04-compile',
  forecast: 'v2-06-forecast',
  round3: 'v2-07-round3',
  reply: 'v2-08-reply',
  recovery: 'v2-09-recovery-head',
  agents: 'v2-10-agents',
  execute: 'v2-11-execute',
  payout: 'v2-12-payout',
  success: 'v2-12b-success',
  refusal: 'v2-13-refusal',
  round4: 'v2-14-round4',
};

type Layer = {segs: Seg[]; zoom: ZoomKeyframe[]; notes?: Note[]; t0?: number; t1?: number; win?: Win; src?: {w: number; h: number; k?: number}};
type Def = {layers: Layer[]; extras?: (tIn: number, totalFrames: number) => React.ReactNode};

const defs = (sc: SbScene): Def => {
  switch (sc.id) {
    /* ---- 0:00 cold open: the board, live: Declined, then Covered, then the pot ---- */
    case 's00-cold-open': {
      const cam = [K(0, R(40, 318, 760))];
      return {
        layers: [
          {segs: [seg(P.round3, 12.6, 15.0)], zoom: cam, t0: 0, t1: 2.5},
          {segs: [seg(P.execute, 24.2, 26.8)], zoom: cam, t0: 2.4, t1: 4.7},
          {segs: [seg(P.payout, 27.9, 30.4)], zoom: cam, t0: 4.6},
        ],
        extras: (_tIn: number, frames: number) => (
          <>
            <Sequence from={Math.round(0.35 * FPS)} durationInFrames={Math.round(1.9 * FPS)} layout="none">
              <Tag text="Ravi's charge: Declined" left={216} bottom={176} tone="bad" frames={Math.round(1.9 * FPS)} />
            </Sequence>
            <Sequence from={Math.round(2.55 * FPS)} durationInFrames={Math.round(1.9 * FPS)} layout="none">
              <Tag text="Round 3: Covered by the buffer" left={216} bottom={176} tone="warn" frames={Math.round(1.9 * FPS)} />
            </Sequence>
            <Sequence from={Math.round(4.2 * FPS)} durationInFrames={frames - Math.round(4.2 * FPS)} layout="none">
              <LowerThird kicker="Turnkeeper" text="Her AI treasurer, on PayPal" frames={frames - Math.round(4.2 * FPS)} bottom={176} left={216} maxWidth={1100} />
            </Sequence>
          </>
        ),
      } as unknown as Def;
    }

    /* ---- 0:20 compile ---- */
    case 's04-compile': {
      const n = P.compile;
      return {
        layers: [
          {
            segs: [seg(n, 0.3, 4.6), seg(n, 5.5, 6.7), seg(n, 7.7, 10.2)],
            zoom: [K(0, R(120, 30, 1000)), K(1.6, R(120, 30, 1000)), K(3.3, R(560, 30, 1000)), K(5.3, R(560, 30, 1000)), K(5.9, R(1000, 30, 880))],
            notes: [
              {t: 0.5, dur: 1.7, rect: {x: 271, y: 98, w: 359, h: 420}, label: 'The circle’s own rules, in Hindi', side: 'right'},
              {t: 3.7, dur: 1.5, rect: {x: 1041, y: 66, w: 164, h: 24}, label: 'Rules parser, no model call', side: 'left'},
              {t: 5.8, dur: 2.0, rect: {x: 1247, y: 101, w: 172, h: 24}, label: 'Frozen: SHA-256 hash', side: 'right'},
            ],
          },
        ],
      };
    }

    /* ---- 0:34 mandates: the member signs for their agent ---- */
    case 's05b-mandates': {
      return {
        layers: [
          {
            segs: [seg(P.mandates, 6.0, 14.8)],
            zoom: [K(0, FULL), K(0.5, FULL), K(1.5, R(960, 215, 960)), K(4.6, R(960, 215, 960)), K(5.5, R(1190, 540, 720))],
          },
        ],
        extras: () => (
          <>
            <HonestChip text="AP2-style mandate, our own implementation" sub="PayPal’s AP2 APIs are not public; we claim no AP2 support" t0={3.0} dur={5.4} left={216} top={580} />
            <HonestChip text="Server-attested signature" sub="HMAC signed by the server, not a key the member holds" t0={5.8} dur={2.8} left={216} top={784} good />
          </>
        ),
      } as unknown as Def;
    }

    /* ---- 0:41 forecast ---- */
    case 's06-forecast': {
      const tFlag = wordAt(sc, 'L07', 'Ravi');
      const tLate = wordAt(sc, 'L07', 'late');
      const tNudge = wordAt(sc, 'L07', 'nudge');
      const from = Math.max(0, 4.7 - (wordAt(sc, 'L07', 'flags') - 0.2));
      return {
        layers: [
          {
            segs: [seg(P.forecast, from, 10.4)],
            zoom: [K(0, R(0, 215, 1100)), K(tFlag + 0.9, R(0, 215, 1100)), K(tFlag + 1.6, R(1140, 740, 760)), K(tNudge - 1.2, R(1140, 740, 760)), K(tNudge - 0.4, R(1190, 960, 720))],
            notes: [
              {t: tFlag - 0.1, dur: 1.2, rect: {x: 646, y: 512, w: 98, h: 32}, label: 'Ravi: at risk', side: 'right'},
              {t: tLate - 0.1, dur: tNudge - 1.3 - (tLate - 0.1), rect: {x: 1539, y: 821, w: 336, h: 56}, label: 'Late in rounds 1 and 2 · last payday 21 days early', side: 'left'},
              {t: tNudge - 0.2, dur: 2.0, rect: {x: 1565, y: 1060, w: 300, h: 120}, label: 'Nudge in Hindi, to Ravi’s phone', side: 'left'},
            ],
          },
        ],
      };
    }

    /* ---- 0:50 round 3 ---- */
    case 's07-round3': {
      const tFive = wordAt(sc, 'L08', 'Five');
      const tDecl = wordAt(sc, 'L08', 'declined');
      const tLab = wordAt(sc, 'L08', 'labelled');
      const tConf = wordAt(sc, 'L08', 'confirmed');
      const from = 13.6 - tFive; // the five charges turn green at file time 13.6
      const aEnd = 14.9;
      const dur = (a: number, b: number) => b - a;
      const sA = aEnd - from;
      return {
        layers: [
          {
            segs: [seg(P.round3, from, aEnd), seg(P.round3, 21.8, 21.8 + (sc.duration - sA))],
            zoom: [K(0, R(0, 215, 1000)), K(tConf - 0.3, R(0, 215, 1000)), K(tConf + 0.5, R(1190, 1500, 720)), K(tDecl - 0.6, R(1190, 1500, 720)), K(tDecl + 0.2, R(0, 330, 1100))],
            notes: [
              {t: tFive - 0.1, dur: dur(tFive - 0.1, tConf - 0.2), rect: {x: 644, y: 335, w: 102, h: 265}, label: 'Five charges complete, live on PayPal sandbox', side: 'left'},
              {t: tConf + 0.6, dur: dur(tConf + 0.6, tDecl - 0.7), rect: {x: 1524, y: 1590, w: 365, h: 250}, label: 'Confirmed through the Agent Toolkit', side: 'left'},
              {t: tDecl + 0.2, dur: 1.4, rect: {x: 646, y: 514, w: 98, h: 38}, label: 'Ravi’s charge declined', side: 'right'},
              {t: tLab - 0.1, dur: sc.duration - 0.35 - (tLab - 0.1), rect: {x: 436, y: 606, w: 200, h: 26}, label: 'API decline: sandbox-injected', side: 'bottom'},
            ],
          },
        ],
      };
    }

    /* ---- 1:01 Ravi replies ---- */
    case 's08-ravi-reply': {
      const tL10 = lineAt(sc, 'L10');
      const from = 1.5; // bubble and toast appear at file time 3.0
      return {
        layers: [
          {
            segs: [seg(P.reply, from, 8.6)],
            zoom: [K(0, R(1000, 1790, 920)), K(tL10 - 0.5, R(1000, 1790, 920)), K(tL10 + 0.2, R(1190, 1250, 720))],
            notes: [
              {t: 2.0, dur: 2.9, rect: {x: 1604, y: 2070, w: 292, h: 46}, label: `Parsed: payday ${captured.payday} (a Friday)`, side: 'left'},
              {t: tL10 + 0.5, dur: 2.7, rect: {x: 1620, y: 1402, w: 262, h: 84}, label: 'A promise with a date', side: 'left'},
            ],
          },
        ],
      };
    }

    /* ---- 1:10 remedies ---- */
    case 's09-remedies': {
      const tBuf = wordAt(sc, 'L11', 'buffer');
      const tInv = wordAt(sc, 'L11', 'invoice');
      const tSwap = wordAt(sc, 'L11', 'swaps');
      const from = 3.4 - (wordAt(sc, 'L11', 'proposes') - 0.1);
      return {
        layers: [
          {
            segs: [seg(P.recovery, Math.max(0, from), Math.max(0, from) + sc.duration)],
            zoom: [K(0, R(0, 330, 1100)), K(tBuf - 0.5, R(0, 330, 1100)), K(tBuf, R(16, 625, 800)), K(tInv - 0.4, R(16, 625, 800)), K(tInv + 0.2, R(480, 625, 800)), K(tSwap - 0.3, R(480, 625, 800)), K(tSwap + 0.3, R(900, 625, 800)), K(sc.duration - 1.0, R(900, 625, 800)), K(sc.duration - 0.3, R(0, 420, 1100))],
            notes: [
              {t: tBuf - 0.1, dur: tInv - 0.4 - tBuf, rect: {x: 29, y: 697, w: 497, h: 100}, label: 'Buffer advance · charter OK', side: 'top'},
              {t: tInv - 0.1, dur: tSwap - 0.4 - tInv, rect: {x: 523, y: 697, w: 483, h: 100}, label: 'Repay by PayPal invoice · charter OK', side: 'top'},
              {t: tSwap - 0.1, dur: 2.0, rect: {x: 1014, y: 697, w: 485, h: 100}, label: 'Swap with Tomas · charter OK', side: 'top'},
            ],
          },
        ],
      };
    }

    /* ---- 1:20 member agents answer ---- */
    case 's10-agents': {
      const tTomas = wordAt(sc, 'L12', 'Tomas');
      const tRavi = wordAt(sc, 'L12', 'Ravi');
      const tFive = wordAt(sc, 'L12', 'Five');
      const tPriya = wordAt(sc, 'L12', 'Priya');
      const sA = tRavi - 0.4; // consents segment length
      const lenB = tPriya - 0.4 - sA - 0.4;
      const fromC = 24.9 - tPriya + (sA + lenB); // the 'Approved by Priya' chip appears at file time 24.9
      return {
        layers: [
          {
            segs: [seg(P.agents, 3.0, 3.0 + sA), seg(P.agents, 14.1, 14.1 + lenB), seg(P.agents, fromC, fromC + (sc.duration - (sA + lenB)))],
            zoom: [K(0, R(580, 700, 720)), K(tRavi - 0.5, R(580, 700, 720)), K(tRavi, R(0, 720, 720)), K(tPriya - 0.9, R(0, 720, 720)), K(tPriya - 0.3, R(900, 700, 720))],
            notes: [
              {t: tTomas - 0.1, dur: tRavi - 0.5 - tTomas, rect: {x: 634, y: 833, w: 646, h: 19}, label: 'Tomas’s agent: yes, inside its rule', side: 'top'},
              {t: tRavi, dur: tFive - 0.2 - tRavi, rect: {x: 31, y: 846, w: 587, h: 20}, label: 'Ravi’s agent asks its human first', side: 'right'},
              {t: tFive - 0.1, dur: tPriya - 0.9 - tFive, rect: {x: 31, y: 806, w: 587, h: 22}, label: '5 yes · 4 needed · quorum', side: 'right'},
              {t: tPriya, dur: 1.2, rect: {x: 1343, y: 812, w: 154, h: 31}, label: 'Priya approves, one tap', side: 'left'},
            ],
          },
        ],
      };
    }

    /* ---- 1:33 execute ---- */
    case 's11-execute': {
      const tBoard = wordAt(sc, 'L13', 'board');
      const tInv = wordAt(sc, 'L13', 'invoice');
      return {
        layers: [
          {
            segs: [seg(P.execute, 4.4, 6.0), seg(P.execute, 24.8, 24.8 + (sc.duration - 1.6))],
            zoom: [K(0, R(0, 330, 1100)), K(1.5, R(0, 330, 1100)), K(tBoard, R(0, 400, 1000)), K(tInv - 0.5, R(0, 400, 1000)), K(tInv + 0.2, R(900, 640, 720))],
            notes: [
              {t: 0.3, dur: 1.3, rect: {x: 642, y: 560, w: 106, h: 36}, label: 'Round 3 covered by the buffer', side: 'right'},
              {t: 1.8, dur: 1.7, rect: {x: 28, y: 512, w: 190, h: 94}, label: 'Swap: Tomas to round 5, Ravi to round 6', side: 'right'},
              {t: tInv, dur: 1.9, rect: {x: 1205, y: 850, w: 295, h: 92}, label: 'PayPal invoice with QR (Agent Toolkit)', side: 'left'},
            ],
          },
        ],
      };
    }

    /* ---- 1:38 payout: the real receipt panel, PENDING, real ids ---- */
    case 's12-payout': {
      const tPay = wordAt(sc, 'L14', 'PayPal');
      const tIds = wordAt(sc, 'L14', 'own');
      return {
        layers: [
          {
            segs: [seg(P.payout, 27.1, 27.1 + sc.duration)],
            zoom: [K(0, R(0, 40, 1000)), K(tPay - 0.4, R(0, 40, 1000)), K(tPay + 0.3, R(1150, 1760, 760))],
            notes: [
              {t: 0.5, dur: 1.9, rect: {x: 543, y: 106, w: 372, h: 24}, label: 'Circle ledger: the payout gate', side: 'bottom'},
              {t: tIds - 0.2, dur: sc.duration - 0.3 - (tIds - 0.2), rect: {x: 1524, y: 1992, w: 365, h: 80}, label: 'PayPal’s own IDs · PENDING when captured', side: 'left'},
            ],
          },
        ],
      };
    }

    /* ---- 1:45 refusal: Try a $230 charge ---- */
    case 's13a-refusal': {
      return {
        layers: [
          {
            segs: [seg(P.refusal, 3.3, 3.3 + sc.duration)],
            zoom: [K(0, R(960, 1760, 960)), K(3.8, R(960, 1760, 960)), K(4.3, R(0, 1760, 960)), K(5.3, R(0, 1760, 960)), K(5.9, R(960, 1760, 960))],
            notes: [
              {t: 4.4, dur: 1.2, rect: {x: 31, y: 2236, w: 700, h: 30}, label: 'Denied: over the $200 mandate', side: 'top'},
              {t: 5.95, dur: 2.1, rect: {x: 1740, y: 2236, w: 150, h: 30}, label: 'PayPal was not called', side: 'top'},
            ],
          },
        ],
      };
    }

    /* ---- 1:59 round 4 ---- */
    case 's13-clean-round': {
      return {
        layers: [
          {
            segs: [seg(P.round4, 11.5, 16.1, 1.5), seg(P.round4, 46.0, 46.0 + (sc.duration - 3.1))],
            zoom: [K(0, R(0, 300, 1100)), K(2.4, R(0, 300, 1100)), K(3.1, R(1060, 45, 840))],
            notes: [
              {t: 1.5, dur: 1.2, rect: {x: 792, y: 330, w: 100, h: 270}, label: 'Six charges, all complete', side: 'right'},
              {t: 3.6, dur: 1.3, rect: {x: 1085, y: 62, w: 262, h: 82}, label: 'Organizer messages typed: 0', side: 'bottom'},
            ],
          },
        ],
        extras: (tIn: number) => (
          <Sequence from={tIn + Math.round(0.2 * FPS)} durationInFrames={Math.round(2.6 * FPS)} layout="none">
            <StripTagFixed text="The Judge Mode button stands in for the calendar" frames={Math.round(2.6 * FPS)} />
          </Sequence>
        ),
      } as unknown as Def;
    }

    default:
      throw new Error(`no capture definition for ${sc.id}`);
  }
};

/** Short coloured tag inside the lower-left of the capture window. */
const Tag: React.FC<{text: string; left: number; bottom: number; tone: 'bad' | 'warn' | 'good'; frames: number}> = ({text, left, bottom, tone, frames}) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 8, frames - 8, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const c = tone === 'bad' ? brand.bad : tone === 'warn' ? brand.accent : brand.good;
  return (
    <div style={{position: 'absolute', left, bottom, opacity: a, fontFamily: brand.font, fontWeight: 800, fontSize: 40, color: brand.white, background: brand.navy, border: `3px solid ${c}`, borderRadius: 16, padding: '8px 24px'}}>
      {text}
    </div>
  );
};

/** One-line larger badge in the pill strip. */
const StripTagFixed: React.FC<{text: string; frames: number}> = ({text, frames}) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 8, frames - 8, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', left: 216, bottom: 176, opacity: a, fontFamily: brand.font, fontWeight: 700, fontSize: 34, color: brand.white, background: 'rgba(8,22,42,0.94)', borderLeft: `8px solid ${brand.accent}`, padding: '8px 22px', borderRadius: 10, whiteSpace: 'nowrap'}}>
      {text}
    </div>
  );
};

export const DesktopCapture: React.FC<{sc: SbScene; totalFrames: number}> = ({sc}) => {
  const tIn = useTIn();
  const {durationInFrames} = useVideoConfig();
  const d = defs(sc);
  return (
    <AbsoluteFill>
      {d.layers.map((l, i) => (
        <FootageLayer key={i} segs={l.segs} zoom={l.zoom} notes={l.notes} src={l.src ?? D} t0={l.t0} t1={l.t1} win={l.win ?? WIN} fade={8} />
      ))}
      {d.extras ? d.extras(tIn, durationInFrames) : null}
    </AbsoluteFill>
  );
};

/* ---------- 1:44-ish: PayPal-side proof, silent: the receipt flipped to SUCCESS and the API read-back ---------- */
export const ProofScene: React.FC<{sc: SbScene}> = ({sc}) => {
  const tIn = useTIn();
  const rec = captured.receipt3_later;
  const win: Win = {x: 96, y: 120, w: 800, h: 660};
  const frames = Math.round(sc.duration * FPS);
  return (
    <AbsoluteFill>
      <div style={{position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, background: brand.navy}} />
      <FootageLayer segs={[seg(P.success, 0.2, 0.2 + sc.duration)]} zoom={[K(0, {x: 1500, y: 1830, w: 420, h: 346})]} src={D} win={win} />
      <ReadbackCard rb={readback} left={960} top={140} width={864} />
      <StripTag text={`Same IDs on both sides · status later read: ${rec.status} (the receipt polls every 5 s)`} t0={0.2} dur={sc.duration - 0.4} left={236} />
      <Sequence from={tIn + 8} durationInFrames={frames - 8} layout="none">
        <ProofFoot />
      </Sequence>
    </AbsoluteFill>
  );
};
const ProofFoot: React.FC = () => (
  <div style={{position: 'absolute', left: 96, right: 96, bottom: 120, fontFamily: brand.font, fontSize: 30, color: brand.mute, lineHeight: 1.35}}>
    Left: Turnkeeper’s receipt panel, after the status poll. Right: what PayPal’s sandbox API returned when we read the batch back. Sandbox only.
  </div>
);

/* ---------- 0:28: phone join ---------- */
const PHONE_WIN = {x: 334, y: 50, w: 453, h: 980};
const PHONE_SRC = {w: 780, h: 1688};
const pseg = (name: string, from: number, to: number, rate = 1): Seg => ({src: `footage/${name}.webm`, from, to, rate});

export const PhoneCapture: React.FC<{sc: SbScene; totalFrames: number}> = ({sc}) => {
  const tFees = wordAt(sc, 'L06', 'fees');
  const tApp = wordAt(sc, 'L06', 'approves');
  const tPP = wordAt(sc, 'L06', 'PayPal');
  const a = 'cap-05-join';
  const b = 'cap-05b-join-paypal';
  const segs: Seg[] = [pseg(a, 33.1, 35.0), pseg(b, 5.3, 6.7), pseg(b, 16.2, 18.7)];
  const zoom: ZoomKeyframe[] = [K(0, PFULL), K(0.3, PFULL), K(1.5, PR(30, 120, 720)), K(1.9, PFULL)];
  const notes: Note[] = [
    {t: 0.3, dur: 1.2, rect: {x: 66, y: 770, w: 648, h: 205}, label: 'Fees up front: ≈ $7.47 per $200', side: 'right'},
    {t: 1.3, dur: 0.6, rect: {x: 66, y: 1308, w: 648, h: 142}, label: 'The plan PayPal will show', side: 'right'},
    {t: 2.0, dur: 1.2, rect: {x: 32, y: 1340, w: 716, h: 98}, label: 'Mini demo circle: Continue to PayPal', side: 'right'},
    {t: 3.5, dur: 2.1, rect: {x: 50, y: 500, w: 680, h: 270}, label: 'PayPal’s own sandbox sign-in (stops here)', side: 'right'},
  ];
  return (
    <AbsoluteFill>
      <PhoneBackdrop win={PHONE_WIN} />
      <FootageLayer segs={segs} zoom={zoom} notes={notes} src={PHONE_SRC} win={PHONE_WIN} rounded={34} badge={false} />
      <MemberPanel
        steps={[
          {t: tFees - 0.1, n: '1', text: 'Fees shown before you consent'},
          {t: tApp - 0.1, n: '2', text: 'Approve the schedule once'},
          {t: tPP - 0.1, n: '3', text: 'On PayPal, in your own account'},
        ]}
      />
      <Footnote text="Live PayPal approval on a $5 mini demo circle; the story circle below is the $200 temple circle." frames={Math.round(sc.duration * FPS)} top={560} left={960} />
    </AbsoluteFill>
  );
};

const MemberPanel: React.FC<{steps: {t: number; n: string; text: string}[]}> = ({steps}) => (
  <div style={{position: 'absolute', left: 960, top: 120, width: 860, fontFamily: brand.font, color: brand.white}}>
    <MemberTitle />
    <div style={{display: 'flex', flexDirection: 'column', gap: 14, marginTop: 14}}>
      {steps.map((s) => (
        <MemberStep key={s.n} {...s} />
      ))}
    </div>
  </div>
);
const MemberTitle: React.FC = () => {
  const p = useReveal(0.1);
  return <div style={{opacity: p, fontSize: 24, fontWeight: 800, letterSpacing: 2, color: brand.accent}}>MEMBER VIEW · PHONE</div>;
};
const MemberStep: React.FC<{t: number; n: string; text: string}> = ({t, n, text}) => {
  const p = useReveal(t, 18);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: 22, opacity: p, translate: `${(1 - p) * 60}px 0`, fontSize: 40, fontWeight: 600}}>
      <div style={{width: 58, height: 58, borderRadius: 29, background: brand.accent, color: brand.navy, fontWeight: 800, fontSize: 32, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>{n}</div>
      <div>{text}</div>
    </div>
  );
};
