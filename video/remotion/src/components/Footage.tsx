import React from 'react';
import {AbsoluteFill, Easing, Freeze, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';
import {useSceneT, useTIn} from '../scene-context';
import type {Note, Rect, Seg, ZoomKeyframe} from '../types';
import {Annotation} from './Annotation';

export type Win = {x: number; y: number; w: number; h: number};
export type Src = {w: number; h: number};

const ease = Easing.bezier(0.45, 0, 0.15, 1);

/** Camera rect at scene time t (s): eased move between keyframes, held outside them. */
export const camAt = (keys: ZoomKeyframe[], t: number, full: Rect): Rect => {
  if (!keys.length) return full;
  const k = [...keys].sort((a, b) => a.t - b.t);
  if (t <= k[0].t) return k[0];
  for (let i = 0; i < k.length - 1; i++) {
    const a = k[i];
    const b = k[i + 1];
    if (t <= b.t) {
      const p = b.t === a.t ? 1 : interpolate(t, [a.t, b.t], [0, 1], {easing: ease});
      return {x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p, w: a.w + (b.w - a.w) * p, h: a.h + (b.h - a.h) * p};
    }
  }
  return k[k.length - 1];
};

/** Window-space transform that frames `r` (source px) so it fills the window, never exposing black edges. */
export const frameRect = (r: Rect, win: Win, src: Src) => {
  const scale = Math.max(win.w / src.w, Math.min(win.w / r.w, win.h / r.h));
  let tx = win.w / 2 - (r.x + r.w / 2) * scale;
  let ty = win.h / 2 - (r.y + r.h / 2) * scale;
  tx = Math.min(0, Math.max(win.w - src.w * scale, tx));
  ty = Math.min(0, Math.max(win.h - src.h * scale, ty));
  return {scale, tx, ty};
};

const secs = (s: Seg) => (s.to - s.from) / (s.rate ?? 1);

export const segsDuration = (segs: Seg[]) => segs.reduce((a, s) => a + secs(s), 0);

const Vid: React.FC<{src: string; trimBefore: number; rate: number; src_: Src}> = ({src, trimBefore, rate, src_}) => (
  <OffthreadVideo
    src={staticFile(src)}
    trimBefore={trimBefore}
    playbackRate={rate}
    muted
    style={{width: src_.w, height: src_.h, display: 'block'}}
  />
);

/**
 * Real footage with a smooth camera (zoom/pan to rects at storyboard times), speed segments, a "sped up" badge
 * for time-compressed stretches, and annotations pointing at real UI. Scene time 0 = scene start; the first
 * `tIn` frames (incoming transition) show the first frame frozen.
 */
export const Footage: React.FC<{
  segs: Seg[];
  zoom: ZoomKeyframe[];
  notes?: Note[];
  win?: Win;
  src: Src;
  /** total scene frames including the transition overlap (tail is frozen on the last frame) */
  totalFrames: number;
  rounded?: number;
}> = ({segs, zoom, notes = [], win = {x: 0, y: 0, w: 1920, h: 1080}, src, totalFrames, rounded = 0}) => {
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const t = useSceneT();
  const full: Rect = {x: 0, y: 0, w: src.w, h: src.h};
  const cam = camAt(zoom, t, full);
  const {scale, tx, ty} = frameRect(cam, win, src);

  let off = tIn;
  const parts: React.ReactNode[] = [];
  const first = segs[0];
  if (tIn > 0) {
    parts.push(
      <Sequence key="pre" from={0} durationInFrames={tIn} layout="none">
        <Freeze frame={0}>
          <Vid src={first.src} trimBefore={Math.round(first.from * fps)} rate={1} src_={src} />
        </Freeze>
      </Sequence>,
    );
  }
  segs.forEach((s, i) => {
    const n = Math.round(secs(s) * fps);
    parts.push(
      <Sequence key={i} from={off} durationInFrames={n} layout="none">
        <Vid src={s.src} trimBefore={Math.round(s.from * fps)} rate={s.rate ?? 1} src_={src} />
      </Sequence>,
    );
    off += n;
  });
  const last = segs[segs.length - 1];
  if (off < totalFrames) {
    parts.push(
      <Sequence key="hold" from={off} durationInFrames={totalFrames - off} layout="none">
        <Freeze frame={0}>
          <Vid src={last.src} trimBefore={Math.max(0, Math.round(last.to * fps) - 2)} rate={1} src_={src} />
        </Freeze>
      </Sequence>,
    );
  }

  // speed badge: any segment played faster than 1.2x
  const badges: React.ReactNode[] = [];
  let o2 = tIn;
  segs.forEach((s, i) => {
    const n = Math.round(secs(s) * fps);
    if ((s.rate ?? 1) > 1.2) {
      badges.push(
        <Sequence key={i} from={o2} durationInFrames={n} layout="none">
          <SpeedBadge rate={s.rate ?? 1} frames={n} />
        </Sequence>,
      );
    }
    o2 += n;
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: win.x,
          top: win.y,
          width: win.w,
          height: win.h,
          overflow: 'hidden',
          borderRadius: rounded,
          background: '#fff',
        }}
      >
        <div style={{position: 'absolute', left: 0, top: 0, transformOrigin: '0 0', translate: `${tx}px ${ty}px`, scale: String(scale)}}>
          {parts}
        </div>
      </div>
      {notes.map((n, i) => {
        const dur = Math.max(1, Math.round(n.dur * fps));
        const box = {
          x: win.x + n.rect.x * scale + tx,
          y: win.y + n.rect.y * scale + ty,
          w: n.rect.w * scale,
          h: n.rect.h * scale,
        };
        return (
          <Sequence key={i} from={tIn + Math.round(n.t * fps)} durationInFrames={dur} layout="none">
            <Annotation box={box} label={n.label} side={n.side} durationInFrames={dur} />
          </Sequence>
        );
      })}
      {badges}
    </AbsoluteFill>
  );
};

const SpeedBadge: React.FC<{rate: number; frames: number}> = ({rate, frames}) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 6, frames - 6, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const label = Number.isInteger(rate) ? `${rate}` : rate.toFixed(1);
  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: 64,
        translate: '-50% 0',
        opacity: a,
        background: brand.accent,
        color: brand.navy,
        fontFamily: brand.font,
        fontWeight: 800,
        fontSize: 26,
        padding: '6px 20px',
        borderRadius: 999,
        boxShadow: '0 6px 18px rgba(0,0,0,.3)',
        letterSpacing: 0.3,
      }}
    >
      {`sped up ${label}×`}
    </div>
  );
};
