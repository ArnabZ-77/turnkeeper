import React from 'react';
import {AbsoluteFill, Sequence} from 'remotion';
import {brand, FPS} from './brand';
import {Footage} from './components/Footage';
import {PhoneBackdrop} from './components/Phone';
import {ReceiptCard, TraceCard} from './components/Cards';
import {Footnote} from './components/Overlays';
import {captured, lineAt, wordAt, type SbScene} from './film-data';
import {useReveal} from './components/Slides';
import {useTIn} from './scene-context';
import type {Note, Rect, Seg, ZoomKeyframe} from './types';

const D = {w: 1920, h: 1080};
const R = (x: number, y: number, w: number): Rect => ({x, y, w, h: (w * 9) / 16});
const PR = (x: number, y: number, w: number): Rect => ({x, y, w, h: (w * 1688) / 780});
const FULL = R(0, 0, 1920);
const PFULL = PR(0, 0, 780);
const K = (t: number, r: Rect): ZoomKeyframe => ({t, ...r});

const F = (name: string) => `footage/${name}.webm`;
const seg = (name: string, from: number, to: number, rate = 1): Seg => ({src: F(name), from, to, rate});

type Def = {
  segs: Seg[];
  zoom: ZoomKeyframe[];
  notes: Note[];
  extras?: (tIn: number, frames: number) => React.ReactNode;
};

const defs = (sc: SbScene): Def => {
  switch (sc.id) {
    case 's04-compile': {
      const n = 'cap-04-compile';
      return {
        segs: [seg(n, 0, 1.8), seg(n, 1.8, 5.8, 4), seg(n, 5.8, 12.155)],
        zoom: [K(0, FULL), K(0.25, FULL), K(0.95, R(110, 60, 1150)), K(2.5, R(110, 60, 1150)), K(3.2, R(560, 40, 1000)), K(5.8, R(560, 40, 1000)), K(6.6, R(640, 380, 820)), K(7.7, R(640, 380, 820)), K(8.2, R(1080, 100, 704))],
        notes: [
          {t: 0.6, dur: 1.9, rect: {x: 271, y: 98, w: 359, h: 420}, label: 'The circle’s own rules, in Hindi', side: 'right'},
          {t: 3.4, dur: 2.2, rect: {x: 1041, y: 66, w: 164, h: 24}, label: 'Rules parser, no model call', side: 'right'},
          {t: 8.2, dur: 0.95, rect: {x: 1247, y: 101, w: 172, h: 24}, label: 'Frozen: SHA-256 hash', side: 'right'},
        ],
      };
    }
    case 's06-forecast': {
      const n = 'cap-06-forecast';
      const tFlag = wordAt(sc, 'L07', 'Ravi');
      const tLate = wordAt(sc, 'L07', 'late');
      const tNudge = wordAt(sc, 'L07', 'nudge');
      return {
        segs: [seg(n, 0.6, 24.6, 8), seg(n, 24.6, 31.609)],
        zoom: [K(0, FULL), K(tFlag + 0.25, FULL), K(tFlag + 1.0, R(1140, 215, 760)), K(tNudge - 0.8, R(1140, 215, 760)), K(tNudge - 0.15, R(1200, 515, 672))],
        notes: [
          {t: 3.2, dur: tFlag + 0.85 - 3.2, rect: {x: 646, y: 512, w: 98, h: 32}, label: 'Ravi: at risk', side: 'right'},
          {t: tFlag + 1.15, dur: 1.5, rect: {x: 1779, y: 238, w: 96, h: 30}, label: 'Wording: TEMPLATE (rules mode)', side: 'left'},
          {t: tLate - 0.1, dur: tNudge - 0.9 - (tLate - 0.1), rect: {x: 1539, y: 297, w: 336, h: 56}, label: 'Late in rounds 1 and 2 · last payday 21 days early', side: 'left'},
          {t: tNudge - 0.1, dur: 2.3, rect: {x: 1565, y: 650, w: 245, h: 160}, label: 'Nudge in Hindi, to Ravi’s phone', side: 'left'},
        ],
      };
    }
    case 's07-round3': {
      const n = 'cap-07-round3';
      const tDecl = wordAt(sc, 'L08', 'declined');
      const tLab = wordAt(sc, 'L08', 'labelled');
      const tc = captured.trace_round3;
      return {
        segs: [seg(n, 0, 1), seg(n, 1, 41, 8), seg(n, 44.8, 50.48)],
        zoom: [K(0, FULL), K(0.9, FULL), K(1.6, R(180, 220, 1000)), K(3.7, R(180, 220, 1000)), K(4.1, FULL), K(tDecl - 0.4, FULL), K(tDecl + 0.3, R(20, 460, 1100))],
        notes: [
          {t: 2.2, dur: 1.9, rect: {x: 644, y: 330, w: 102, h: 275}, label: 'Five charges complete, live on PayPal sandbox', side: 'left'},
          {t: tDecl, dur: 1.0, rect: {x: 646, y: 515, w: 98, h: 38}, label: 'Ravi’s charge declined', side: 'right'},
          {t: tLab - 0.2, dur: 2.6, rect: {x: 441, y: 612, w: 190, h: 22}, label: 'API decline: sandbox-injected', side: 'bottom'},
        ],
        extras: (tIn) => (
          <Sequence from={tIn + Math.round(4.1 * FPS)} durationInFrames={Math.round((tDecl - 0.1 - 4.1) * FPS)} layout="none">
            <TraceCard ai={tc.ai} collect={tc.collect} tools={tc.tools} frames={Math.round((tDecl - 0.1 - 4.1) * FPS)} left={40} top={640} />
          </Sequence>
        ),
      };
    }
    case 's08-ravi-reply': {
      const n = 'cap-08-reply';
      const tL10 = lineAt(sc, 'L10');
      return {
        segs: [seg(n, 0, 8.3)],
        zoom: [K(0, FULL), K(0.5, FULL), K(1.2, R(1180, 615, 720))],
        notes: [
          {t: 2.5, dur: 2.6, rect: {x: 1625, y: 850, w: 270, h: 46}, label: `Parsed: payday ${captured.payday} (a Friday)`, side: 'left'},
          {t: tL10 - 0.1, dur: 3.0, rect: {x: 1622, y: 858, w: 232, h: 38}, label: 'A promise with a date', side: 'left'},
        ],
        extras: () => null,
      };
    }
    case 's09-remedies': {
      const n = 'cap-09-recovery';
      const tBuf = wordAt(sc, 'L11', 'buffer');
      const tInv = wordAt(sc, 'L11', 'invoice');
      const tSwap = wordAt(sc, 'L11', 'swaps');
      return {
        segs: [seg(n, 0.2, 13.9, 4), seg(n, 13.9, 19.8, 0.8)],
        zoom: [K(0, FULL), K(3.8, FULL), K(4.5, R(16, 640, 704)), K(tInv - 0.3, R(16, 640, 704)), K(tInv + 0.3, R(500, 640, 704)), K(tSwap - 0.1, R(500, 640, 704)), K(tSwap + 0.5, R(800, 640, 704)), K(10.5, R(800, 640, 704)), K(11.2, FULL)],
        notes: [
          {t: 4.3, dur: tInv - 0.1 - 4.3, rect: {x: 32, y: 697, w: 480, h: 102}, label: 'Buffer advance · charter OK', side: 'top'},
          {t: tInv, dur: 2.1, rect: {x: 523, y: 697, w: 483, h: 102}, label: 'Repay by PayPal invoice · charter OK', side: 'top'},
          {t: tSwap, dur: 2.3, rect: {x: 1015, y: 697, w: 483, h: 102}, label: 'Swap with Tomas · charter OK', side: 'top'},
          {t: 11.25, dur: 0.95, rect: {x: 1397, y: 638, w: 102, h: 24}, label: 'Proposed in rules mode', side: 'left'},
        ],
      };
    }
    case 's10-consent-vote-approve': {
      const n = 'cap-10-approve';
      return {
        segs: [seg(n, 1.2, 3.4, 2), seg(n, 3.4, 6.5, 2), seg(n, 6.5, 10.32, 2)],
        zoom: [K(0, R(300, 630, 800)), K(1.2, R(300, 630, 800)), K(1.7, R(16, 630, 800)), K(3.0, R(16, 630, 800)), K(3.5, R(700, 630, 800))],
        notes: [
          {t: 0.95, dur: 1.3, rect: {x: 602, y: 830, w: 335, h: 32}, label: 'Ravi and Tomas consent', side: 'top'},
          {t: 2.4, dur: 1.2, rect: {x: 31, y: 808, w: 555, h: 36}, label: 'Votes: 4 yes of 4 needed · quorum reached', side: 'top'},
          {t: 3.75, dur: 0.85, rect: {x: 1343, y: 830, w: 154, h: 34}, label: 'Priya approved, one tap', side: 'left'},
        ],
      };
    }
    case 's11-execute': {
      const n = 'cap-11-execute';
      return {
        segs: [seg(n, 0.5, 3.9, 2), seg(n, 3.9, 18.5, 8), seg(n, 18.5, 20.5)],
        zoom: [K(0, FULL), K(0.8, FULL), K(1.3, R(20, 380, 1000)), K(3.0, R(20, 380, 1000)), K(3.5, R(1000, 640, 720))],
        notes: [
          {t: 1.3, dur: 1.3, rect: {x: 646, y: 560, w: 98, h: 38}, label: 'Round 3 covered by the buffer', side: 'right'},
          {t: 2.1, dur: 1.1, rect: {x: 32, y: 512, w: 170, h: 92}, label: 'Swap: Tomas to round 5, Ravi to round 6', side: 'right'},
          {t: 3.7, dur: 1.7, rect: {x: 1202, y: 806, w: 296, h: 92}, label: 'PayPal invoice with QR (Agent Toolkit)', side: 'left'},
        ],
      };
    }
    case 's12-payout': {
      const n = 'cap-12-payout';
      const fr = Math.round((5.95 - 2.0) * FPS);
      return {
        segs: [seg(n, 0.2, 6.4, 4), seg(n, 6.4, 12.6)],
        zoom: [K(0, R(16, 45, 960)), K(1.8, R(16, 45, 960)), K(2.4, R(1180, 640, 720)), K(5.7, R(1180, 640, 720)), K(6.5, FULL)],
        notes: [
          {t: 0.4, dur: 1.5, rect: {x: 543, y: 106, w: 372, h: 24}, label: 'Circle ledger: the payout gate', side: 'bottom'},
          {t: 2.6, dur: 3.0, rect: {x: 1525, y: 795, w: 360, h: 95}, label: 'PENDING when captured · fee $0.25', side: 'top'},
        ],
        extras: (tIn) => (
          <Sequence from={tIn + Math.round(2.0 * FPS)} durationInFrames={fr} layout="none">
            <ReceiptCard receipt={captured.receipt3} frames={fr} left={96} top={250} />
          </Sequence>
        ),
      };
    }
    case 's13-clean-round': {
      const n = 'cap-13-round4';
      return {
        segs: [seg(n, 0.2, 30.2, 10), seg(n, 46.0, 48.7)],
        zoom: [K(0, FULL), K(3.5, FULL), K(4.0, R(1060, 45, 840))],
        notes: [
          {t: 2.9, dur: 1.0, rect: {x: 792, y: 330, w: 100, h: 270}, label: 'Six charges, all complete', side: 'right'},
          {t: 4.3, dur: 1.3, rect: {x: 1085, y: 62, w: 262, h: 70}, label: 'Organizer messages typed: 0', side: 'bottom'},
        ],
        extras: (tIn) => (
          <Sequence from={tIn + Math.round(0.3 * FPS)} durationInFrames={Math.round(4.5 * FPS)} layout="none">
            <Footnote text="The Judge Mode button stands in for the calendar; the organizer does nothing." frames={Math.round(4.5 * FPS)} bottom={232} />
          </Sequence>
        ),
      };
    }
    default:
      throw new Error(`no capture definition for ${sc.id}`);
  }
};

export const DesktopCapture: React.FC<{sc: SbScene; totalFrames: number}> = ({sc, totalFrames}) => {
  const tIn = useTIn();
  const d = defs(sc);
  return (
    <AbsoluteFill>
      <Footage segs={d.segs} zoom={d.zoom} notes={d.notes} src={D} totalFrames={totalFrames} />
      {d.extras ? d.extras(tIn, totalFrames) : null}
    </AbsoluteFill>
  );
};

/* ---------- s05: phone ---------- */
const PHONE_WIN = {x: 334, y: 50, w: 453, h: 980};
const PHONE_SRC = {w: 780, h: 1688};

export const PhoneCapture: React.FC<{sc: SbScene; totalFrames: number}> = ({sc, totalFrames}) => {
  const tFees = wordAt(sc, 'L06', 'fees');
  const tApp = wordAt(sc, 'L06', 'approves');
  const tPP = wordAt(sc, 'L06', 'PayPal');
  const a = 'cap-05-join';
  const b = 'cap-05b-join-paypal';
  const segs: Seg[] = [seg(a, 33.1, 35.0), seg(b, 5.3, 6.7), seg(b, 16.2, 18.7)];
  const zoom: ZoomKeyframe[] = [K(0, PFULL), K(0.3, PFULL), K(1.5, PR(30, 120, 720)), K(1.9, PFULL)];
  const notes: Note[] = [
    {t: 0.3, dur: 1.2, rect: {x: 66, y: 770, w: 648, h: 205}, label: 'Fees up front: ≈ $7.47 per $200', side: 'right'},
    {t: 1.3, dur: 0.6, rect: {x: 66, y: 1308, w: 648, h: 142}, label: 'The plan PayPal will show', side: 'right'},
    {t: 2.0, dur: 1.2, rect: {x: 32, y: 1340, w: 716, h: 98}, label: 'Judge mini-circle: Continue to PayPal', side: 'right'},
    {t: 3.5, dur: 2.1, rect: {x: 50, y: 500, w: 680, h: 270}, label: 'PayPal’s own sandbox sign-in (stops here)', side: 'right'},
  ];
  return (
    <AbsoluteFill>
      <PhoneBackdrop win={PHONE_WIN} />
      <Footage segs={segs} zoom={zoom} notes={notes} src={PHONE_SRC} win={PHONE_WIN} totalFrames={totalFrames} rounded={34} />
      <MemberPanel steps={[
        {t: tFees - 0.1, n: '1', text: 'Fees shown before you consent'},
        {t: tApp - 0.1, n: '2', text: 'Approve the schedule once'},
        {t: tPP - 0.1, n: '3', text: 'On PayPal, in your own account'},
      ]} />
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

