// Resumable chunked render (survives a headless-chrome crash, e.g. after the laptop sleeps).
//   node scripts/render-chunks.mjs [chunkFrames=450] [concurrency=3]
// video-only chunks -> out/chunks/c_NNN.mp4 (skipped if present), audio -> out/chunks/audio.aac, then ffmpeg concat + mux.
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const chunk = Number(process.argv[2] ?? 450);
const concurrency = Number(process.argv[3] ?? 3);
const outDir = path.resolve('out/chunks');
fs.mkdirSync(outDir, {recursive: true});
const serveUrl = await bundle({entryPoint: path.resolve('remotion/src/index.ts'), publicDir: path.resolve('public'), onProgress: () => {}});
const comp = await selectComposition({serveUrl, id: 'Turnkeeper', port: 3462});
const N = comp.durationInFrames;
console.log('frames', N);
const common = {composition: comp, serveUrl, concurrency, port: 3462, chromiumOptions: {gl: 'swangle'}};
for (let s = 0, i = 0; s < N; s += chunk, i++) {
  const e = Math.min(N - 1, s + chunk - 1);
  const f = path.join(outDir, `c_${String(i).padStart(3, '0')}.mp4`);
  if (fs.existsSync(f)) continue;
  for (let attempt = 1; ; attempt++) {
    try {
      await renderMedia({...common, codec: 'h264', crf: 18, pixelFormat: 'yuv420p', muted: true, frameRange: [s, e], outputLocation: f.replace('.mp4', '.part.mp4')});
      fs.renameSync(f.replace('.mp4', '.part.mp4'), f);
      console.log('done', i, s, e);
      break;
    } catch (err) {
      console.log('chunk', i, 'attempt', attempt, 'failed:', String(err).slice(0, 200));
      if (attempt >= 4) process.exit(1);
    }
  }
}
const aud = path.join(outDir, 'audio.wav');
if (!fs.existsSync(aud)) {
  await renderMedia({...common, codec: 'wav', outputLocation: aud});
}
const list = fs.readdirSync(outDir).filter((x) => /^c_\d+\.mp4$/.test(x)).sort().map((x) => `file '${path.join(outDir, x).split(path.sep).join('/')}'`).join('\n');
fs.writeFileSync(path.join(outDir, 'list.txt'), list);
const r = spawnSync('ffmpeg', ['-hide_banner', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', path.join(outDir, 'list.txt'), '-i', aud, '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart', path.resolve('out/turnkeeper_v2.mp4')], {stdio: 'inherit'});
process.exit(r.status ?? 1);
