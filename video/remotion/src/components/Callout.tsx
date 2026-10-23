import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';

export type CalloutProps = {
  /** arrow tip, in OUTPUT px (already projected through the zoom) */
  x: number;
  y: number;
  label: string;
  side?: 'left' | 'right' | 'top' | 'bottom';
  /** frames visible, used for the fade-out */
  durationInFrames: number;
};

const ARROW = 140;
const DIRS = {left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1]} as const;
const LABEL_SHIFT = {
  left: 'translate(-100%, -50%) translateX(-14px)',
  right: 'translate(0, -50%) translateX(14px)',
  top: 'translate(-50%, -100%) translateY(-14px)',
  bottom: 'translate(-50%, 0) translateY(14px)',
} as const;

export const Callout: React.FC<CalloutProps> = ({x, y, label, side = 'left', durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const inP = spring({frame, fps, config: {damping: 18, stiffness: 160}});
  const out = interpolate(frame, [durationInFrames - 8, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const dir = DIRS[side];
  const len = ARROW * inP;
  const sx = x + dir[0] * len;
  const sy = y + dir[1] * len;
  const angle = Math.atan2(y - sy, x - sx) || Math.atan2(-dir[1], -dir[0]);
  const head = 22;
  const deg = (angle * 180) / Math.PI;

  return (
    <div style={{position: 'absolute', inset: 0, opacity: out * Math.min(1, inP * 1.5), pointerEvents: 'none'}}>
      <svg width="100%" height="100%" style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
        <circle cx={x} cy={y} r={14 + 10 * inP} fill="none" stroke={brand.accent} strokeWidth={5} />
        <line
          x1={sx}
          y1={sy}
          x2={x - Math.cos(angle) * 26}
          y2={y - Math.sin(angle) * 26}
          stroke={brand.accent}
          strokeWidth={8}
          strokeLinecap="round"
        />
        <polygon
          points={`0,0 ${-head},${-head / 1.6} ${-head},${head / 1.6}`}
          fill={brand.accent}
          transform={`translate(${x - Math.cos(angle) * 22}, ${y - Math.sin(angle) * 22}) rotate(${deg})`}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          left: sx,
          top: sy,
          transform: LABEL_SHIFT[side],
          background: brand.navy,
          color: brand.white,
          border: `3px solid ${brand.accent}`,
          borderRadius: 14,
          padding: '14px 24px',
          fontFamily: brand.font,
          fontWeight: 600,
          fontSize: 36,
          whiteSpace: 'nowrap',
          boxShadow: '0 10px 30px rgba(0,0,0,.35)',
        }}
      >
        {label}
      </div>
    </div>
  );
};
