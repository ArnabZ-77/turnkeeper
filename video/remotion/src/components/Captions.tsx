import React, {useMemo} from 'react';
import {createTikTokStyleCaptions, type Caption} from '@remotion/captions';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from '../brand';

/**
 * Plain caption text -> word-level Caption[] spread across the voice window (weighted by word length).
 * Swap for real word timings (e.g. Whisper via @remotion/install-whisper-cpp) if needed.
 */
export const textToCaptions = (text: string, startMs: number, endMs: number): Caption[] => {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const weights = words.map((w) => w.length + 2);
  const total = weights.reduce((a, b) => a + b, 0) || 1;
  let cursor = startMs;
  return words.map((w, i) => {
    const len = ((endMs - startMs) * weights[i]) / total;
    const c: Caption = {
      text: (i === 0 ? '' : ' ') + w,
      startMs: cursor,
      endMs: cursor + len,
      timestampMs: cursor,
      confidence: 1,
    };
    cursor += len;
    return c;
  });
};

/** Bottom captions, max 2 lines, high contrast; the word being spoken is tinted with the accent colour. */
export const Captions: React.FC<{text: string; startMs: number; endMs: number}> = ({text, startMs, endMs}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const nowMs = (frame / fps) * 1000;
  const pages = useMemo(
    () =>
      createTikTokStyleCaptions({
        captions: textToCaptions(text, startMs, endMs),
        combineTokensWithinMilliseconds: 2200,
      }).pages,
    [text, startMs, endMs],
  );
  const page = pages.find((p) => nowMs >= p.startMs && nowMs < p.startMs + p.durationMs);
  if (!page) return null;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 72, pointerEvents: 'none'}}>
      <div
        style={{
          maxWidth: 1500,
          background: 'rgba(11,31,58,0.9)',
          color: brand.white,
          fontFamily: brand.font,
          fontWeight: 600,
          fontSize: 46,
          lineHeight: 1.3,
          padding: '16px 32px',
          borderRadius: 16,
          textAlign: 'center',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
          whiteSpace: 'pre-wrap',
        }}
      >
        {page.tokens.map((tok, i) => (
          <span key={i} style={{color: nowMs >= tok.fromMs && nowMs < tok.toMs ? brand.accent : brand.white}}>
            {tok.text}
          </span>
        ))}
      </div>
    </AbsoluteFill>
  );
};
