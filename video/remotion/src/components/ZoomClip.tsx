import React from 'react';
import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {HEIGHT, WIDTH} from '../brand';
import type {CalloutSpec, Rect, ZoomKeyframe} from '../types';
import {Callout} from './Callout';

const FULL: Rect = {x: 0, y: 0, w: WIDTH, h: HEIGHT};

/** Camera rect at time t (seconds): eased interpolation between keyframes; full frame if none. */
export const cameraAt = (keys: ZoomKeyframe[] | undefined, t: number): Rect => {
  if (!keys || keys.length === 0) return FULL;
  const k = [...keys].sort((a, b) => a.t - b.t);
  if (t <= k[0].t) return k[0];
  for (let i = 0; i < k.length - 1; i++) {
    const a = k[i];
    const b = k[i + 1];
    if (t <= b.t) {
      const p = interpolate(t, [a.t, b.t], [0, 1], {easing: Easing.inOut(Easing.cubic)});
      return {x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p, w: a.w + (b.w - a.w) * p, h: a.h + (b.h - a.h) * p};
    }
  }
  return k[k.length - 1];
};

/** Rect -> {scale, tx, ty} so the rect fills the frame (aspect preserved), clamped so no black edges show. */
export const rectToTransform = (r: Rect) => {
  const scale = Math.max(1, Math.min(WIDTH / r.w, HEIGHT / r.h));
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;
  let tx = WIDTH / 2 - cx * scale;
  let ty = HEIGHT / 2 - cy * scale;
  tx = Math.min(0, Math.max(WIDTH - WIDTH * scale, tx));
  ty = Math.min(0, Math.max(HEIGHT - HEIGHT * scale, ty));
  return {scale, tx, ty};
};

export const ZoomClip: React.FC<{
  src: string;
  zoom?: ZoomKeyframe[];
  callouts?: CalloutSpec[];
  startFromSec?: number;
  playbackRate?: number;
}> = ({src, zoom, callouts = [], startFromSec = 0, playbackRate = 1}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {scale, tx, ty} = rectToTransform(cameraAt(zoom, frame / fps));
  return (
    <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
      <AbsoluteFill style={{transformOrigin: '0 0', transform: `translate(${tx}px, ${ty}px) scale(${scale})`}}>
        <OffthreadVideo
          src={src.startsWith('http') ? src : staticFile(src)}
          trimBefore={Math.round(startFromSec * fps)}
          playbackRate={playbackRate}
          muted
          style={{width: WIDTH, height: HEIGHT}}
        />
      </AbsoluteFill>
      {callouts.map((c, i) => {
        const dur = Math.max(1, Math.round((c.dur ?? 2) * fps));
        // project the source-video point through the current camera so the arrow tracks the zoom
        return (
          <Sequence key={i} from={Math.round(c.t * fps)} durationInFrames={dur} layout="none">
            <Callout x={c.x * scale + tx} y={c.y * scale + ty} label={c.label} side={c.side} durationInFrames={dur} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
