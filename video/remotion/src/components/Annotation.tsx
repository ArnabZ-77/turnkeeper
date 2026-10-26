import React from 'react';
import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand, HEIGHT, WIDTH} from '../brand';
import type {Rect} from '../types';

export type AnnotationProps = {
  /** box in OUTPUT (screen) px, already projected through the camera */
  box: Rect;
  label: string;
  side?: 'left' | 'right' | 'top' | 'bottom';
  /** frames visible (fade-out in the last 8) */
  durationInFrames: number;
};

const GAP = 46;
const PAD = 8;

/** Highlights a real UI rectangle and labels it with a short connector. Rendered per frame inside a Sequence. */
export const Annotation: React.FC<AnnotationProps> = ({box, label, side = 'right', durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame, fps, config: {damping: 20, stiffness: 170, mass: 0.8}});
  const out = interpolate(frame, [durationInFrames - 9, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const pulse = 0.5 + 0.5 * Math.sin((frame / fps) * 2 * Math.PI * 1.1);
  const bx = box.x - PAD;
  const by = box.y - PAD;
  const bw = box.w + PAD * 2;
  const bh = box.h + PAD * 2;
  const cx = bx + bw / 2;
  const cy = by + bh / 2;

  // anchor on the box edge, label offset by GAP
  const anchor = {left: [bx, cy], right: [bx + bw, cy], top: [cx, by], bottom: [cx, by + bh]}[side];
  const lx = Math.min(Math.max(anchor[0] + (side === 'right' ? GAP : side === 'left' ? -GAP : 0), 8), WIDTH - 8);
  const ly = Math.min(Math.max(anchor[1] + (side === 'bottom' ? GAP : side === 'top' ? -GAP : 0), 130), HEIGHT - 300);
  const labelStyle: React.CSSProperties = {
    position: 'absolute',
    maxWidth: side === 'right' ? Math.min(720, WIDTH - lx - 56) : side === 'left' ? Math.min(720, lx - 56) : 720,
    width: 'max-content',
    boxSizing: 'border-box',
    background: brand.navy,
    color: brand.white,
    border: `3px solid ${brand.accent}`,
    borderRadius: 16,
    padding: '12px 24px',
    fontFamily: brand.font,
    fontWeight: 600,
    fontSize: 34,
    lineHeight: 1.2,
    boxShadow: '0 12px 34px rgba(0,0,0,.38)',
    ...(side === 'right' && {left: lx, top: ly, translate: '0 -50%'}),
    ...(side === 'left' && {right: WIDTH - lx, top: ly, translate: '0 -50%'}),
    ...(side === 'top' && {left: Math.min(Math.max(lx, 440), WIDTH - 440), top: ly, translate: '-50% -100%'}),
    ...(side === 'bottom' && {left: Math.min(Math.max(lx, 440), WIDTH - 440), top: ly, translate: '-50% 0'}),
  };

  return (
    <div style={{position: 'absolute', inset: 0, opacity: out * Math.min(1, p * 1.6), pointerEvents: 'none'}}>
      <svg width={WIDTH} height={HEIGHT} style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
        <rect
          x={bx - 4 * (1 - p)}
          y={by - 4 * (1 - p)}
          width={bw + 8 * (1 - p)}
          height={bh + 8 * (1 - p)}
          rx={12}
          fill={`rgba(245,166,35,${0.07 + 0.05 * pulse})`}
          stroke={brand.accent}
          strokeWidth={4}
          style={{filter: `drop-shadow(0 0 ${6 + 8 * pulse}px rgba(245,166,35,.75))`}}
        />
        <line x1={anchor[0]} y1={anchor[1]} x2={lx} y2={ly} stroke={brand.accent} strokeWidth={4} strokeLinecap="round" />
        <circle cx={anchor[0]} cy={anchor[1]} r={7} fill={brand.accent} />
      </svg>
      <div style={labelStyle}>{label}</div>
    </div>
  );
};

export const _ease = Easing.bezier(0.16, 1, 0.3, 1);
