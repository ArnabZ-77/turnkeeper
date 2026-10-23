import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';

export const TitleCard: React.FC<{title: string; subtitle?: string}> = ({title, subtitle}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = spring({frame, fps, config: {damping: 200, mass: 0.8}});
  const s = spring({frame: frame - 8, fps, config: {damping: 200}});
  const bar = spring({frame: frame - 4, fps, config: {damping: 30, stiffness: 120}});

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(1200px 700px at 30% 20%, #12345f 0%, ${brand.navy} 60%)`,
        justifyContent: 'center',
        alignItems: 'center',
        fontFamily: brand.font,
        color: brand.white,
      }}
    >
      <div style={{textAlign: 'center', maxWidth: 1500}}>
        <div
          style={{
            fontSize: 120,
            fontWeight: 800,
            letterSpacing: -2,
            lineHeight: 1.05,
            opacity: t,
            transform: `translateY(${interpolate(t, [0, 1], [40, 0])}px) scale(${interpolate(t, [0, 1], [0.96, 1])})`,
          }}
        >
          {title}
        </div>
        <div
          style={{
            height: 8,
            width: 220,
            margin: '36px auto',
            borderRadius: 4,
            background: `linear-gradient(90deg, ${brand.blue}, ${brand.accent})`,
            transform: `scaleX(${bar})`,
          }}
        />
        {subtitle ? (
          <div
            style={{
              fontSize: 48,
              fontWeight: 400,
              opacity: s * 0.92,
              transform: `translateY(${interpolate(s, [0, 1], [24, 0])}px)`,
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
