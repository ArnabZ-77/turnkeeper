import React from 'react';
import type {Caption} from '@remotion/captions';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';
import {captions} from '../film-data';

type Page = {from: number; to: number; words: {t: string; s: number; e: number}[]};

/** Page captions into <= 2 lines (~ <= 84 chars), breaking after punctuation where possible. */
export const pageLine = (lineId: string): Page[] => {
  const words = captions[lineId].words;
  const LIMIT = 104; // two lines of ~58 characters at 46 px in a 1490 px box
  const size = (a: number, b: number) => words.slice(a, b).reduce((n, w) => n + w.t.length + 1, 0);
  const split = (a: number, b: number): [number, number][] => {
    if (size(a, b) <= LIMIT) return [[a, b]];
    // prefer the sentence end, else the comma, nearest the middle of the span
    const mid = a + (b - a) / 2;
    const cands = (re: RegExp) => {
      const out: number[] = [];
      for (let i = a; i < b - 1; i++) if (re.test(words[i].t)) out.push(i + 1);
      return out;
    };
    let pool = cands(/[.!?।]$/);
    if (!pool.length) pool = cands(/[,;:]$/);
    const k = pool.length ? pool.reduce((best, c) => (Math.abs(c - mid) < Math.abs(best - mid) ? c : best)) : Math.round(mid);
    return [...split(a, k), ...split(k, b)];
  };
  return split(0, words.length).map(([a, b]) => ({from: words[a].s, to: words[b - 1].e, words: words.slice(a, b)}));
};

export const asCaptions = (lineId: string): Caption[] =>
  captions[lineId].words.map((w) => ({text: ' ' + w.t, startMs: w.s * 1000, endMs: w.e * 1000, timestampMs: w.s * 1000, confidence: 1}));

const HI_TRANSLATION: Record<string, string> = {H01: "Salary comes on Friday, I'll pay then."};

export type CaptionLine = {id: string; abs: number};

/** Burned-in captions synced to the narration WAVs (word timings from script/captions.py). Max two lines. */
export type Place = {style?: React.CSSProperties; fontSize?: number; maxWidth?: number};

export const FilmCaptions: React.FC<{lines: CaptionLine[]; placeFor?: (id: string) => Place | undefined}> = ({lines, placeFor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const now = frame / fps;
  const active = lines.find((l) => now >= l.abs - 0.05 && now < l.abs + captions[l.id].dur + 0.35);
  if (!active) return null;
  const rel = now - active.abs;
  const pages = pageLine(active.id);
  let page = pages[0];
  for (const p of pages) if (rel >= p.from - 0.05) page = p;
  const isHi = active.id === 'H01';
  const fade = Math.min(1, Math.max(0, (rel - (page.from - 0.05)) / 0.12));
  const fadeOut = Math.min(1, Math.max(0, (captions[active.id].dur + 0.35 - rel) / 0.2));
  const style: React.CSSProperties = {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 16,
    display: 'flex',
    justifyContent: 'center',
    pointerEvents: 'none',
    ...(placeFor?.(active.id)?.style ?? {}),
  };
  const place = placeFor?.(active.id);
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div style={style}>
        <div
          style={{
            maxWidth: place?.maxWidth ?? 1560,
            background: 'rgba(8,22,42,0.93)',
            color: brand.white,
            fontFamily: isHi ? brand.fontDeva : brand.font,
            fontWeight: 600,
            fontSize: isHi ? 50 : place?.fontSize ?? 40,
            lineHeight: 1.24,
            padding: '8px 28px 10px',
            borderRadius: 18,
            textAlign: 'center',
            opacity: fade * fadeOut,
            boxShadow: '0 10px 30px rgba(0,0,0,.4)',
            border: '1.5px solid rgba(255,255,255,0.12)',
            overflow: 'hidden',
          }}
        >
          <div>
            {page.words.map((w, i) => {
              const on = rel >= w.s && rel < w.e;
              return (
                <span key={i} style={{color: on ? brand.accent : brand.white}}>
                  {(i ? ' ' : '') + w.t}
                </span>
              );
            })}
          </div>
          {isHi ? (
            <div style={{fontFamily: brand.font, fontWeight: 400, fontStyle: 'italic', fontSize: 30, color: brand.mute, marginTop: 2}}>
              {HI_TRANSLATION[active.id]}
            </div>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};
