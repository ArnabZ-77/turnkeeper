import React from 'react';
import {Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';
import {useTIn} from '../scene-context';

/** One pill, top right, clear of the capture window: sandbox label, PayPal mode and AI mode together. */
export const SandboxBadge: React.FC<{modes?: boolean}> = ({modes}) => {
  const frame = useCurrentFrame();
  const a = interpolate(frame, [0, 8], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', top: 10, right: 24, opacity: a * 0.95, pointerEvents: 'none'}}>
      <div
        style={{
          fontFamily: brand.font,
          fontWeight: 600,
          fontSize: 22,
          color: brand.white,
          background: 'rgba(11,31,58,0.92)',
          border: '1.5px solid rgba(245,166,35,0.75)',
          borderRadius: 999,
          padding: '4px 18px',
          whiteSpace: 'nowrap',
          letterSpacing: 0.2,
        }}
      >
        <span style={{color: brand.accent}}>●</span>&nbsp; {modes ? 'PayPal sandbox live · rules mode · no real money' : 'Sandbox demo · no real money'}
      </div>
    </div>
  );
};

/** Lower-third source caption (every statistic carries one). Slides in from the left. */
export const LowerThird: React.FC<{text: string; kicker?: string; frames: number; bottom?: number; left?: number; maxWidth?: number}> = ({
  text,
  kicker = 'Source',
  frames,
  bottom = 232,
  left = 96,
  maxWidth = 1300,
}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const inP = interpolate(f, [0, 12], [0, 1], {easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateRight: 'clamp'});
  const outP = interpolate(f, [frames - 10, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  void fps;
  return (
    <div
      style={{
        position: 'absolute',
        left,
        bottom,
        opacity: Math.min(inP, outP),
        translate: `${(1 - inP) * -60}px 0`,
        display: 'flex',
        alignItems: 'stretch',
        maxWidth,
        fontFamily: brand.font,
        boxShadow: '0 8px 26px rgba(0,0,0,.35)',
        borderRadius: 10,
        overflow: 'hidden',
      }}
    >
      <div style={{background: brand.accent, color: brand.navy, fontWeight: 800, fontSize: 22, padding: '10px 16px', letterSpacing: 1, textTransform: 'uppercase', display: 'flex', alignItems: 'center'}}>
        {kicker}
      </div>
      <div style={{background: 'rgba(8,22,42,0.94)', color: brand.white, fontWeight: 600, fontSize: 28, padding: '10px 20px', lineHeight: 1.25}}>{text}</div>
    </div>
  );
};

/** Small footnote line, lower left. */
export const Footnote: React.FC<{text: string; frames: number; bottom?: number; top?: number; left?: number}> = ({text, frames, bottom = 232, top, left = 96}) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 10, frames - 10, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div
      style={{
        position: 'absolute',
        left,
        ...(top === undefined ? {bottom} : {top}),
        opacity: a,
        fontFamily: brand.font,
        fontWeight: 600,
        fontSize: top === undefined ? 26 : 24,
        color: brand.white,
        background: 'rgba(8,22,42,0.9)',
        borderLeft: `6px solid ${brand.accent}`,
        padding: '10px 18px',
        borderRadius: 8,
        maxWidth: 1100,
        lineHeight: 1.25,
      }}
    >
      {text}
    </div>
  );
};

/** Small persistent Turnkeeper lockup, top left of the pill strip (clear of the capture window). */
export const Lockup: React.FC = () => (
  <div style={{position: 'absolute', top: 9, left: 24, display: 'flex', alignItems: 'center', gap: 10, fontFamily: brand.font, color: brand.white, opacity: 0.95, pointerEvents: 'none'}}>
    <div style={{width: 26, height: 26, borderRadius: 7, background: `linear-gradient(135deg, ${brand.blue}, ${brand.accent})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 17, color: brand.white}}>T</div>
    <div style={{fontWeight: 800, fontSize: 26, letterSpacing: -0.4}}>Turnkeeper</div>
  </div>
);

/** Large honest-label chip used on the mandate scene. Visible from t0 for dur seconds (scene time, inside a Sequence-less layer). */
export const HonestChip: React.FC<{text: string; sub?: string; t0: number; dur: number; left: number; top: number; good?: boolean}> = ({text, sub, t0, dur, left, top, good}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const a0 = tIn + Math.round(t0 * fps);
  const a1 = a0 + Math.round(dur * fps);
  if (frame < a0 || frame >= a1) return null;
  const o = interpolate(frame, [a0, a0 + 8, a1 - 8, a1], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const slide = interpolate(frame, [a0, a0 + 10], [40, 0], {extrapolateRight: 'clamp', easing: Easing.bezier(0.16, 1, 0.3, 1)});
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        opacity: o,
        translate: `${-slide}px 0`,
        fontFamily: brand.font,
        background: brand.navy,
        border: `3px solid ${good ? brand.good : brand.accent}`,
        borderRadius: 18,
        padding: '12px 26px 14px',
        color: brand.white,
        boxShadow: '0 14px 40px rgba(0,0,0,.45)',
        maxWidth: 690,
      }}
    >
      <div style={{fontWeight: 800, fontSize: 38, lineHeight: 1.15}}>{text}</div>
      {sub ? <div style={{fontWeight: 400, fontSize: 24, color: brand.mute, marginTop: 4, lineHeight: 1.25}}>{sub}</div> : null}
    </div>
  );
};
