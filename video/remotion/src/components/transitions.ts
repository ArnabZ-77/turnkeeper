import {linearTiming, springTiming, type TransitionPresentation} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {slide} from '@remotion/transitions/slide';
import type {TransitionKind} from '../types';

/** Transition presets used between storyboard scenes. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const presentationFor = (kind: TransitionKind): TransitionPresentation<any> =>
  kind === 'slide' ? slide({direction: 'from-right'}) : fade();

export const timingFor = (kind: TransitionKind, frames: number) =>
  kind === 'slide'
    ? springTiming({config: {damping: 200}, durationInFrames: frames, durationRestThreshold: 0.001})
    : linearTiming({durationInFrames: frames});
