import React from 'react';
import {AbsoluteFill} from 'remotion';
import {brand} from './brand';
import {FootageLayer, type Win} from './components/Footage';
import {Backdrop} from './components/Slides';

/** 1280x720 thumbnail: our own UI (member agents answering inside their mandates) beside the title. No third-party logos. */
export const Thumb: React.FC = () => {
  const win: Win = {x: 560, y: 190, w: 680, h: 310};
  return (
    <AbsoluteFill>
      <Backdrop />
      <div style={{position: 'absolute', left: 70, top: 120, width: 470, fontFamily: brand.font, color: brand.white}}>
        <div style={{fontSize: 84, fontWeight: 800, letterSpacing: -3, lineHeight: 1}}>Turnkeeper</div>
        <div style={{height: 8, width: 190, margin: '22px 0', borderRadius: 4, background: `linear-gradient(90deg, ${brand.blue}, ${brand.accent})`}} />
        <div style={{fontSize: 38, fontWeight: 600, lineHeight: 1.2, color: brand.accent}}>Her AI treasurer, on PayPal</div>
        <div style={{fontSize: 27, fontWeight: 400, marginTop: 22, lineHeight: 1.3, color: brand.mute}}>Member agents act inside signed mandates. The model never decides.</div>
      </div>
      <FootageLayer
        segs={[{src: 'footage/v2/v2-10-agents-s.mp4', from: 16.5, to: 17.5, rate: 1}]}
        zoom={[{t: 0, x: 16, y: 625, w: 760, h: 346}]}
        src={{w: 1920, h: 2300, k: 1.5}}
        win={win}
        badge={false}
        rounded={16}
      />
      <div style={{position: 'absolute', left: 560, top: 530, fontFamily: brand.font, fontSize: 24, fontWeight: 600, color: brand.white}}>
        <span style={{color: brand.accent}}>●</span>&nbsp; PayPal sandbox · AP2-style mandates, our own implementation
      </div>
      <div style={{position: 'absolute', left: 70, bottom: 56, fontFamily: brand.font, fontSize: 24, color: brand.mute}}>Sandbox only · no real money</div>
    </AbsoluteFill>
  );
};
