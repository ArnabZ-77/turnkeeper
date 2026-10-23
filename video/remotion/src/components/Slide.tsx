import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';

export const Slide: React.FC<{title: string; bullets?: string[]}> = ({title, bullets = []}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const head = spring({frame, fps, config: {damping: 200}});
  return (
    <AbsoluteFill style={{background: brand.white, fontFamily: brand.font, color: brand.navy, padding: '140px 180px'}}>
      <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: 24, background: brand.blue}} />
      <div
        style={{
          fontSize: 84,
          fontWeight: 800,
          letterSpacing: -1,
          opacity: head,
          transform: `translateX(${interpolate(head, [0, 1], [-40, 0])}px)`,
        }}
      >
        {title}
      </div>
      <div style={{marginTop: 64, display: 'flex', flexDirection: 'column', gap: 36}}>
        {bullets.map((b, i) => {
          const p = spring({frame: frame - 10 - i * 8, fps, config: {damping: 200}});
          return (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 28,
                fontSize: 52,
                opacity: p,
                transform: `translateY(${interpolate(p, [0, 1], [20, 0])}px)`,
              }}
            >
              <div style={{width: 18, height: 18, borderRadius: 9, background: brand.accent, flexShrink: 0}} />
              <div>{b}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
