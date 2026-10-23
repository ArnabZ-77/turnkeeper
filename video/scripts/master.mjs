// Final mastering: two-pass ffmpeg loudnorm to -14 LUFS integrated, -1.5 dBTP, video stream copied.
//   node scripts/master.mjs [in=out/main.mp4] [out=out/final.mp4]
import {spawnSync} from 'node:child_process';

const [inp = 'out/main.mp4', out = 'out/final.mp4'] = process.argv.slice(2);
const target = 'I=-14:TP=-1.5:LRA=11';

const pass1 = spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-i', inp, '-af', `loudnorm=${target}:print_format=json`, '-f', 'null', '-'], {encoding: 'utf8'});
const m = pass1.stderr.match(/\{[\s\S]*?"target_offset"[\s\S]*?\}/);
if (!m) { console.error(pass1.stderr); process.exit(1); }
const s = JSON.parse(m[0]);
console.log(`measured: I=${s.input_i} LUFS, TP=${s.input_tp} dBTP, LRA=${s.input_lra}`);
const filter = `loudnorm=${target}:measured_I=${s.input_i}:measured_TP=${s.input_tp}:measured_LRA=${s.input_lra}:measured_thresh=${s.input_thresh}:offset=${s.target_offset}:linear=true`;
const pass2 = spawnSync('ffmpeg', ['-hide_banner', '-y', '-loglevel', 'error', '-i', inp, '-c:v', 'copy', '-af', filter, '-ar', '48000', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', out], {stdio: 'inherit'});
if (pass2.status !== 0) process.exit(pass2.status ?? 1);
console.log(`wrote ${out}`);
