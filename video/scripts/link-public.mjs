// Media lives in Remotion's public dir (video/public/{footage,audio}) because Remotion copies
// the public dir into its bundle and cannot re-create symlinks/junctions without admin rights.
// For convenience video/footage and video/audio are directory junctions pointing INTO public/,
// so capture/TTS can write to video/footage and video/audio. Junctions need no admin on Windows.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const name of ['footage', 'audio']) {
  const real = path.join(root, 'public', name);
  const link = path.join(root, name);
  fs.mkdirSync(real, {recursive: true});
  let st;
  try { st = fs.lstatSync(link); } catch {}
  if (st?.isSymbolicLink()) { console.log(`ok   ${name} -> public/${name}`); continue; }
  if (st) { console.warn(`skip ${name}: exists as a real folder; move its files into public/${name}`); continue; }
  fs.symlinkSync(real, link, process.platform === 'win32' ? 'junction' : 'dir');
  console.log(`link ${name} -> public/${name}`);
}
