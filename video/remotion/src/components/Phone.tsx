import React from 'react';
import {AbsoluteFill} from 'remotion';
import {Backdrop} from './Slides';
import type {Win} from './Footage';

/** Navy backdrop plus a phone body around the screen window `win`. */
export const PhoneBackdrop: React.FC<{win: Win}> = ({win}) => {
  const bezel = 18;
  return (
    <AbsoluteFill>
      <Backdrop />
      <div
        style={{
          position: 'absolute',
          left: win.x - bezel,
          top: win.y - bezel,
          width: win.w + bezel * 2,
          height: win.h + bezel * 2,
          borderRadius: 34 + bezel,
          background: 'linear-gradient(145deg, #2b3a52, #0a1424)',
          border: '2px solid #46597a',
          boxShadow: '0 40px 90px rgba(0,0,0,.55), inset 0 0 0 3px #0a1424',
        }}
      />
      <div style={{position: 'absolute', left: win.x + win.w / 2 - 44, top: win.y - 11, width: 88, height: 8, borderRadius: 4, background: '#05101f'}} />
    </AbsoluteFill>
  );
};
