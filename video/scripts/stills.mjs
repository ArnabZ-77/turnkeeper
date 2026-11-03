// Fast stills: bundle once, render many frames.   node scripts/stills.mjs s07-round3:2.4,6 s10-agents:3 ...
import path from 'node:path';
import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';

const root = path.resolve(import.meta.dirname, '..');
const sb = JSON.parse(fs.readFileSync(path.join(root, 'script/storyboard.json'), 'utf8'));
const specs = process.argv.slice(2);
const bundled = await bundle({entryPoint: path.join(root, 'remotion/src/index.ts'), publicDir: path.join(root, 'public')});
const comp = await selectComposition({serveUrl: bundled, id: 'Turnkeeper', port: 3459});
fs.mkdirSync(path.join(root, 'out/stills'), {recursive: true});
for (const spec of specs) {
  const [id, ts] = spec.split(':');
  const sc = sb.scenes.find((s) => s.id === id);
  if (!sc) { console.error('no scene', id); continue; }
  for (const t of ts.split(',')) {
    const frame = Math.min(comp.durationInFrames - 1, Math.round((sc.start + Number(t)) * 30));
    const output = path.join(root, `out/stills/${id}_${t}.png`);
    await renderStill({composition: comp, serveUrl: bundled, output, frame, port: 3459});
    console.log('wrote', output);
  }
}
