import React, {useEffect, useState} from 'react';
import {AbsoluteFill, Audio, Easing, Sequence, continueRender, delayRender, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {FPS} from './brand';
import {DesktopCapture, PhoneCapture} from './CaptureScenes';
import {FilmCaptions} from './components/FilmCaptions';
import {SandboxBadge} from './components/Overlays';
import {AiLayerSlide, ApisSlide, ArchSlide, EvidenceSlide, HookSlide, OutroSlide, PriorArtSlide, ProblemSlide, TitleSlide} from './components/Slides';
import {fontsReady} from './fonts';
import {SceneCtx} from './scene-context';
import {storyboard, type SbScene} from './film-data';

export const TOTAL_FRAMES = Math.ceil(storyboard.total_sec * FPS);
const frameOf = (s: number) => Math.round(s * FPS);

const FADE_F = 12;
const SLIDE_F = 13;

const tInFor = (sc: SbScene, i: number) => {
  if (i === 0) return 0;
  const k = sc.transition?.kind ?? 'none';
  return k === 'fade' ? FADE_F : k === 'slide' ? SLIDE_F : 0;
};

const Body: React.FC<{sc: SbScene; totalFrames: number}> = ({sc, totalFrames}) => {
  switch (sc.id) {
    case 's01-hook':
      return <HookSlide sc={sc} />;
    case 's02-priya-stats':
      return <ProblemSlide sc={sc} />;
    case 's03-title':
      return <TitleSlide />;
    case 's05-join-phone':
      return <PhoneCapture sc={sc} totalFrames={totalFrames} />;
    case 's14-architecture':
      return <ArchSlide sc={sc} />;
    case 's15-apis':
      return <ApisSlide sc={sc} />;
    case 's16-ai-layer':
      return <AiLayerSlide sc={sc} />;
    case 's17-evidence':
      return <EvidenceSlide sc={sc} />;
    case 's18-prior-art-honesty':
      return <PriorArtSlide sc={sc} />;
    case 's19-outro':
      return <OutroSlide />;
    default:
      return <DesktopCapture sc={sc} totalFrames={totalFrames} />;
  }
};

/** Scene wrapper: the incoming transition (fade or slide, 12-13 frames) overlaps the previous scene's silent tail. */
const SceneWrap: React.FC<{sc: SbScene; i: number}> = ({sc, i}) => {
  const frame = useCurrentFrame();
  const tIn = tInFor(sc, i);
  const total = frameOf(sc.duration) + tIn;
  let style: React.CSSProperties = {};
  if (i === 0) {
    style = {opacity: interpolate(frame, [0, 10], [0, 1], {extrapolateRight: 'clamp'})};
  } else if (tIn > 0) {
    const p = interpolate(frame, [0, tIn], [0, 1], {extrapolateRight: 'clamp', easing: Easing.bezier(0.16, 1, 0.3, 1)});
    style = sc.transition?.kind === 'slide' ? {translate: `${(1 - p) * 1920}px 0`, boxShadow: '-30px 0 60px rgba(0,0,0,.35)'} : {opacity: p};
  }
  return (
    <SceneCtx.Provider value={{tIn}}>
      <AbsoluteFill style={style}>
        <Body sc={sc} totalFrames={total} />
      </AbsoluteFill>
    </SceneCtx.Provider>
  );
};

const PRODUCT = new Set(['s04-compile', 's05-join-phone', 's06-forecast', 's07-round3', 's08-ravi-reply', 's09-remedies', 's10-consent-vote-approve', 's11-execute', 's12-payout', 's13-clean-round']);

const Badges: React.FC = () => {
  const frame = useCurrentFrame();
  const sc = storyboard.scenes.find((s) => frame >= frameOf(s.start) && frame < frameOf(s.end)) ?? storyboard.scenes[0];
  const desktopProduct = PRODUCT.has(sc.id) && sc.id !== 's05-join-phone';
  return <SandboxBadge modes={PRODUCT.has(sc.id)} shiftX={desktopProduct ? -80 : 0} />;
};

export const Film: React.FC = () => {
  const [handle] = useState(() => delayRender('Loading fonts'));
  useEffect(() => {
    fontsReady.then(() => continueRender(handle)).catch((e) => {
      console.error(e);
      continueRender(handle);
    });
  }, [handle]);

  const lines = storyboard.scenes.flatMap((s) => s.lines.map((l) => ({id: l.id, abs: l.abs})));
  return (
    <AbsoluteFill style={{background: '#0b1f3a'}}>
      {storyboard.scenes.map((sc, i) => {
        const tIn = tInFor(sc, i);
        return (
          <Sequence key={sc.id} from={frameOf(sc.start) - tIn} durationInFrames={frameOf(sc.duration) + tIn} name={sc.id}>
            <SceneWrap sc={sc} i={i} />
          </Sequence>
        );
      })}
      {lines.map((l) => (
        <Sequence key={l.id} from={frameOf(l.abs)} name={`vo ${l.id}`} layout="none">
          <Audio src={staticFile(`audio/vo/${l.id}.wav`)} />
        </Sequence>
      ))}
      <Badges />
      <FilmCaptions lines={lines} placeFor={(id) => (id === 'L06' ? {style: {left: 900, right: 40, bottom: 70}, fontSize: 40, maxWidth: 940} : undefined)} />
    </AbsoluteFill>
  );
};
