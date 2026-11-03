import React from 'react';
import {AbsoluteFill, Easing, Freeze, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';
import {useSceneT, useTIn} from '../scene-context';
import type {Note, Rect, Seg, ZoomKeyframe} from '../types';
import {Annotation} from './Annotation';

export type Win = {x: number; y: number; w: number; h: number};
/** Source size in layout px; k = native pixels per layout px (2 for a deviceScaleFactor 2 capture). */
export type Src = {w: number; h: number; k?: number};

/** v2 layout: the capture sits in the upper part of the frame; a pill strip above and a caption band below stay clear of the UI. */
export const WIN: Win = {x: 192, y: 56, w: 1536, h: 864};

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
  const scale = Math.max(win.w / src.w, win.h / src.h, Math.min(win.w / r.w, win.h / r.h));
  let tx = win.w / 2 - (r.x + r.w / 2) * scale;
  let ty = win.h / 2 - (r.y + r.h / 2) * scale;
  tx = Math.min(0, Math.max(win.w - src.w * scale, tx));
  ty = Math.min(0, Math.max(win.h - src.h * scale, ty));
  return {scale, tx, ty};
};

const secs = (s: Seg) => (s.to - s.from) / (s.rate ?? 1);

export const segsDuration = (segs: Seg[]) => segs.reduce((a, s) => a + secs(s), 0);

const Vid: React.FC<{src: string; trimBefore: number; rate: number; src_: Src}> = ({src, trimBefore, rate, src_}) => {
  const k = src_.k ?? 1;
  return <OffthreadVideo src={staticFile(src)} trimBefore={trimBefore} playbackRate={rate} muted style={{width: src_.w * k, height: src_.h * k, display: 'block'}} />;
};

/**
 * One piece of real footage with its own camera (zoom/pan), speed segments, "sped up" badge and annotations that box
 * real UI. `t0`..`t1` is the scene-time span it is on screen; neighbouring layers cross-fade over `fade` frames.
 * The first `tIn` frames of a scene (incoming transition) show the first frame frozen.
 */
export const FootageLayer: React.FC<{
  segs: Seg[];
  zoom: ZoomKeyframe[];
  notes?: Note[];
  win?: Win;
  src: Src;
  t0?: number;
  t1?: number;
  fade?: number;
  rounded?: number;
  badge?: boolean;
}> = ({segs, zoom, notes = [], win = WIN, src, t0 = 0, t1 = Infinity, fade = 6, rounded = 12, badge = true}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const tIn = useTIn();
  const t = useSceneT();
  const full: Rect = {x: 0, y: 0, w: src.w, h: src.h};
  const cam = camAt(zoom, t, full);
  const {scale, tx, ty} = frameRect(cam, win, src);

  const start = tIn + Math.round(t0 * fps);
  const end = Number.isFinite(t1) ? tIn + Math.round(t1 * fps) : Infinity;
  if (frame < start || frame >= end) return null;
  const finite = Number.isFinite(end);
  const opacity = finite
    ? interpolate(frame, [start, start + (t0 > 0 ? fade : 0) + 0.001, end - fade, end], [t0 > 0 ? 0 : 1, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
    : interpolate(frame, [start, start + (t0 > 0 ? fade : 0) + 0.001], [t0 > 0 ? 0 : 1, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  let off = start;
  const parts: React.ReactNode[] = [];
  const first = segs[0];
  if (t0 === 0 && tIn > 0) {
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
  const holdTo = finite ? end : off + 3000;
  if (off < holdTo) {
    parts.push(
      <Sequence key="hold" from={off} durationInFrames={holdTo - off} layout="none">
        <Freeze frame={0}>
          <Vid src={last.src} trimBefore={Math.max(0, Math.round(last.to * fps) - 2)} rate={1} src_={src} />
        </Freeze>
      </Sequence>,
    );
  }

  const badges: React.ReactNode[] = [];
  let o2 = start;
  segs.forEach((s, i) => {
    const n = Math.round(secs(s) * fps);
    if (badge && (s.rate ?? 1) > 1.2) {
      badges.push(
        <Sequence key={i} from={o2} durationInFrames={n} layout="none">
          <SpeedBadge rate={s.rate ?? 1} frames={n} left={win.x + 60} />
        </Sequence>,
      );
    }
    o2 += n;
  });

  return (
    <AbsoluteFill style={{opacity}}>
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
          boxShadow: '0 14px 50px rgba(0,0,0,.45), 0 0 0 1.5px rgba(255,255,255,.14)',
        }}
      >
        <div style={{position: 'absolute', left: 0, top: 0, transformOrigin: '0 0', translate: `${tx}px ${ty}px`, scale: String(scale / (src.k ?? 1))}}>{parts}</div>
      </div>
      {notes.map((n, i) => {
        const dur = Math.max(1, Math.round(n.dur * fps));
        const box = {x: win.x + n.rect.x * scale + tx, y: win.y + n.rect.y * scale + ty, w: n.rect.w * scale, h: n.rect.h * scale};
        return (
          <Sequence key={i} from={tIn + Math.round(n.t * fps)} durationInFrames={dur} layout="none">
            <Annotation box={box} label={n.label} side={n.side} durationInFrames={dur} winBox={win} />
          </Sequence>
        );
      })}
      {badges}
    </AbsoluteFill>
  );
};

const SpeedBadge: React.FC<{rate: number; frames: number; left: number}> = ({rate, frames, left}) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 6, frames - 6, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const label = Number.isInteger(rate) ? `${rate}` : rate.toFixed(1);
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top: 10,
        opacity: a,
        background: brand.accent,
        color: brand.navy,
        fontFamily: brand.font,
        fontWeight: 800,
        fontSize: 24,
        padding: '4px 18px',
        borderRadius: 999,
        letterSpacing: 0.3,
      }}
    >
      {`sped up ${label}×`}
    </div>
  );
};

/** A short explanatory tag in the strip above the window (never over the UI). */
export const StripTag: React.FC<{text: string; t0: number; dur: number; left?: number}> = ({text, t0, dur, left = WIN.x}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tIn = useTIn();
  const a0 = tIn + Math.round(t0 * fps);
  const a1 = a0 + Math.round(dur * fps);
  if (frame < a0 || frame >= a1) return null;
  const o = interpolate(frame, [a0, a0 + 6, a1 - 6, a1], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', left, top: 10, opacity: o, fontFamily: brand.font, fontWeight: 600, fontSize: 24, color: brand.white, background: 'rgba(8,22,42,0.9)', borderLeft: `6px solid ${brand.accent}`, padding: '4px 16px', borderRadius: 8, whiteSpace: 'nowrap'}}>
      {text}
    </div>
  );
};
