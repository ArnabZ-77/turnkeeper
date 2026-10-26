import React from 'react';
import {Composition, staticFile, type CalculateMetadataFunction} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './brand';
import {Film, TOTAL_FRAMES} from './Film';
import {Main, sceneStarts} from './Main';
import type {MainProps} from './types';
import defaultStoryboard from '../storyboard.test.json';

/**
 * Resolves scene lengths: fixed durationSec wins; otherwise voice length (from
 * `${audioDir}/durations.json`, written by tts/say.py) + padSec + voice delay.
 */
const calculateMetadata: CalculateMetadataFunction<MainProps> = async ({props}) => {
  let voiceSec: Record<string, number> = {};
  if (props.audioDir && props.scenes.some((s) => s.voiceId)) {
    const res = await fetch(staticFile(`${props.audioDir}/durations.json`));
    if (!res.ok) throw new Error(`Could not load ${props.audioDir}/durations.json (${res.status}). Run tts/say.py first.`);
    voiceSec = await res.json();
  }
  const pad = props.padSec ?? 0.6;
  const frames = props.scenes.map((s) => {
    if (s.durationSec) return Math.round(s.durationSec * FPS);
    const v = s.voiceId ? voiceSec[s.voiceId] : undefined;
    if (v === undefined) throw new Error(`Scene "${s.id}" needs durationSec or a voiceId present in durations.json`);
    return Math.round((v + (s.voiceDelaySec ?? 0.3) + pad) * FPS);
  });
  const resolved = {frames, voiceSec};
  const {total} = sceneStarts(props, frames, FPS);
  return {durationInFrames: Math.max(1, total), props: {...props, resolved}};
};

export const RemotionRoot: React.FC = () => (
  <>
    {/* The demo film: scenes, timings and narration come from script/storyboard.json (see edl in CaptureScenes.tsx). */}
    <Composition id="Turnkeeper" component={Film} width={WIDTH} height={HEIGHT} fps={FPS} durationInFrames={TOTAL_FRAMES} />
    {/* Toolchain smoke test (npm run render:test). */}
    <Composition
      id="Main"
      component={Main}
      width={WIDTH}
      height={HEIGHT}
      fps={FPS}
      durationInFrames={FPS * 6}
      defaultProps={defaultStoryboard as MainProps}
      calculateMetadata={calculateMetadata}
    />
  </>
);
