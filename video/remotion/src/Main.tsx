import React, {useEffect, useState} from 'react';
import {AbsoluteFill, Audio, Sequence, continueRender, delayRender, interpolate, staticFile, useVideoConfig} from 'remotion';
import {TransitionSeries} from '@remotion/transitions';
import {Captions} from './components/Captions';
import {Slide} from './components/Slide';
import {TitleCard} from './components/TitleCard';
import {ZoomClip} from './components/ZoomClip';
import {presentationFor, timingFor} from './components/transitions';
import {fontsReady} from './fonts';
import type {MainProps, Scene} from './types';

const DEFAULT_VOICE_DELAY = 0.3;

export const transitionFrames = (props: MainProps, fps: number) => Math.round((props.transitionSec ?? 0.5) * fps);

/** Start frame of every scene on the global timeline (transitions overlap neighbouring scenes). */
export const sceneStarts = (props: MainProps, frames: number[], fps: number) => {
  const tf = transitionFrames(props, fps);
  const starts: number[] = [];
  let cursor = 0;
  props.scenes.forEach((s, i) => {
    starts.push(cursor);
    cursor += frames[i];
    if (i < props.scenes.length - 1 && (s.transition ?? 'fade') !== 'none') cursor -= tf;
  });
  return {starts, total: cursor};
};

const SceneBody: React.FC<{scene: Scene}> = ({scene}) => {
  switch (scene.type) {
    case 'title':
      return <TitleCard title={scene.title} subtitle={scene.subtitle} />;
    case 'slide':
      return <Slide title={scene.title} bullets={scene.bullets} />;
    case 'clip':
      return (
        <ZoomClip
          src={scene.src}
          zoom={scene.zoom}
          callouts={scene.callouts}
          startFromSec={scene.startFromSec}
          playbackRate={scene.playbackRate}
        />
      );
  }
};

const SceneView: React.FC<{scene: Scene; frames: number; props: MainProps}> = ({scene, frames, props}) => {
  const {fps} = useVideoConfig();
  const delaySec = scene.voiceDelaySec ?? DEFAULT_VOICE_DELAY;
  const voiceSec = scene.voiceId ? props.resolved?.voiceSec[scene.voiceId] : undefined;
  const capStart = delaySec * 1000;
  const capEnd = voiceSec ? (delaySec + voiceSec) * 1000 : (frames / fps) * 1000 - 300;
  return (
    <AbsoluteFill>
      <SceneBody scene={scene} />
      {scene.voiceId && props.audioDir ? (
        <Sequence from={Math.round(delaySec * fps)} layout="none">
          <Audio src={staticFile(`${props.audioDir}/${scene.voiceId}.wav`)} />
        </Sequence>
      ) : null}
      {scene.caption ? <Captions text={scene.caption} startMs={capStart} endMs={capEnd} /> : null}
    </AbsoluteFill>
  );
};

export const Main: React.FC<MainProps> = (props) => {
  const {fps} = useVideoConfig();
  const [handle] = useState(() => delayRender('Loading Inter font'));
  useEffect(() => {
    fontsReady.then(() => continueRender(handle)).catch((e) => {
      console.error(e);
      continueRender(handle);
    });
  }, [handle]);

  const frames = props.resolved?.frames ?? props.scenes.map((s) => Math.round((s.durationSec ?? 3) * fps));
  const tf = transitionFrames(props, fps);
  const {starts} = sceneStarts(props, frames, fps);

  // Global voice windows (frames) for music ducking.
  const voiceWindows = props.scenes.flatMap((s, i) => {
    const v = s.voiceId ? props.resolved?.voiceSec[s.voiceId] : undefined;
    if (!v) return [];
    const a = starts[i] + Math.round((s.voiceDelaySec ?? DEFAULT_VOICE_DELAY) * fps);
    return [[a, a + Math.round(v * fps)] as const];
  });
  const music = props.music;
  const musicVolume = (f: number) => {
    const full = music?.volume ?? 0.25;
    const duck = music?.duckedVolume ?? 0.06;
    const ramp = 8; // frames
    let level = full;
    for (const [a, b] of voiceWindows) {
      const k = interpolate(f, [a - ramp, a, b, b + ramp], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
      level = Math.min(level, full - (full - duck) * k);
    }
    return level;
  };

  const children: React.ReactNode[] = [];
  props.scenes.forEach((scene, i) => {
    children.push(
      <TransitionSeries.Sequence key={`s-${scene.id}`} durationInFrames={frames[i]}>
        <SceneView scene={scene} frames={frames[i]} props={props} />
      </TransitionSeries.Sequence>,
    );
    const kind = scene.transition ?? 'fade';
    if (i < props.scenes.length - 1 && kind !== 'none') {
      children.push(
        <TransitionSeries.Transition key={`t-${scene.id}`} presentation={presentationFor(kind)} timing={timingFor(kind, tf)} />,
      );
    }
  });

  return (
    <AbsoluteFill style={{background: '#0b1f3a'}}>
      <TransitionSeries>{children}</TransitionSeries>
      {music ? <Audio src={staticFile(music.src)} loop volume={musicVolume} /> : null}
    </AbsoluteFill>
  );
};
