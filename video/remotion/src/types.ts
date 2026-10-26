export type Rect = {x: number; y: number; w: number; h: number};

/** Zoom keyframe: at t seconds (relative to scene start) frame the rect (source-video px, 1920x1080). */
export type ZoomKeyframe = Rect & {t: number};

export type CalloutSpec = {
  /** start, seconds relative to scene start */
  t: number;
  /** seconds visible (default 2) */
  dur?: number;
  /** point the arrow tip at, in source-video px (follows the zoom) */
  x: number;
  y: number;
  label: string;
  /** side the label sits on relative to the point (default 'left') */
  side?: 'left' | 'right' | 'top' | 'bottom';
};

export type TransitionKind = 'fade' | 'slide' | 'none';

type SceneBase = {
  id: string;
  /** Fixed scene length. If omitted: voice length + padSec (needs voiceId + durations.json). */
  durationSec?: number;
  /** id of a line in the TTS script -> `${audioDir}/${voiceId}.wav` */
  voiceId?: string;
  /** seconds of silence before the voice starts (default 0.3) */
  voiceDelaySec?: number;
  /** burned-in caption text (defaults to nothing) */
  caption?: string;
  /** transition INTO the next scene */
  transition?: TransitionKind;
};

export type TitleScene = SceneBase & {type: 'title'; title: string; subtitle?: string};
export type SlideScene = SceneBase & {type: 'slide'; title: string; bullets?: string[]};
export type ClipScene = SceneBase & {
  type: 'clip';
  /** path under public/, e.g. footage/scene-01.webm */
  src: string;
  /** seconds to skip at the start of the clip */
  startFromSec?: number;
  playbackRate?: number;
  zoom?: ZoomKeyframe[];
  callouts?: CalloutSpec[];
};

export type Scene = TitleScene | SlideScene | ClipScene;

export type Storyboard = {
  /** dir under public/ holding <voiceId>.wav + durations.json (output of tts/say.py) */
  audioDir?: string;
  /** seconds added after the voice when durationSec is omitted (default 0.6) */
  padSec?: number;
  /** transition length in seconds (default 0.5) */
  transitionSec?: number;
  music?: {src: string; volume?: number; duckedVolume?: number};
  scenes: Scene[];
};

/** Resolved by calculateMetadata: per-scene frames + voice durations. */
export type MainProps = Storyboard & {
  resolved?: {frames: number[]; voiceSec: Record<string, number>};
};

// ---- v1 film types (edit decision list) ----
/** One stretch of source footage: [from,to] source seconds played at `rate` (1 = real time). */
export type Seg = {src: string; from: number; to: number; rate?: number};
/** Annotation that points at a real UI rectangle (source px). */
export type Note = {
  t: number;
  dur: number;
  rect: Rect;
  label: string;
  side?: 'left' | 'right' | 'top' | 'bottom';
};
