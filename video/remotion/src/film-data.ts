import storyboardJson from '../../script/storyboard.json';
import captionsJson from './data/captions.json';
import capturedJson from './data/captured.json';
import readbackJson from './data/paypal_readback.json';

export type SbLine = {id: string; at: number; abs: number; dur: number; text: string};
export type SbScene = {
  id: string;
  kind: string;
  start: number;
  duration: number;
  end: number;
  transition?: {kind: 'fade' | 'slide' | 'none'; sec: number};
  lines: SbLine[];
  captions?: {line: string}[];
  overlays?: {type: string; line?: string; offset?: number; text: string}[];
};
export type CapWord = {t: string; s: number; e: number};
export type CapLine = {text: string; dur: number; speech: [number, number]; words: CapWord[]};

export const storyboard = storyboardJson as unknown as {scenes: SbScene[]; total_sec: number};
export const captions = captionsJson as unknown as Record<string, CapLine>;
export const captured = capturedJson as unknown as {
  circle: string;
  run: string;
  hash: string;
  invoice: string;
  payday: string;
  receipt3: Receipt;
  receipt3_later: Receipt;
  receipt4: Receipt;
  refusal: {headline: string; reason: string; chip: string};
  mandate_labels: string[];
};
export const readback = readbackJson as unknown as {
  request: string;
  host: string;
  payout_batch_id: string;
  batch_status: string;
  amount: string;
  fees: string;
  currency: string;
  time_completed: string;
  payout_item_id: string;
  transaction_status: string;
  item_amount: string;
};
export type Receipt = {title: string; status: string; amount: string; recipient: string; item: string; batch: string; paid_at: string};

export const scene = (id: string): SbScene => {
  const s = storyboard.scenes.find((x) => x.id === id);
  if (!s) throw new Error(`scene ${id} not in storyboard.json`);
  return s;
};

/** Scene-relative start (s) of a narration line. */
export const lineAt = (sc: SbScene, id: string): number => {
  const l = sc.lines.find((x) => x.id === id);
  if (!l) throw new Error(`line ${id} not in ${sc.id}`);
  return l.at;
};

const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

/** Scene-relative time (s) at which the voice says a word that starts with `prefix` (nth match, default first). */
export const wordAt = (sc: SbScene, lineId: string, prefix: string, nth = 0): number => {
  const cap = captions[lineId];
  const hits = cap.words.filter((w) => norm(w.t).startsWith(norm(prefix)));
  if (hits.length <= nth) throw new Error(`word "${prefix}" #${nth} not found in ${lineId}`);
  return lineAt(sc, lineId) + hits[nth].s;
};
