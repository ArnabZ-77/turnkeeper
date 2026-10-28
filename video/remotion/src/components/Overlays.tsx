import React from 'react';
import {Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';

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
