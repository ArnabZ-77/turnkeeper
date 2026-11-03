/**
 * Playwright screencast capture for the Turnkeeper demo.
 *
 *   node capture/capture.ts --storyboard capture/storyboard.test.json [--out footage] [--headed] [--only scene-01]
 *                           [--base-url http://localhost:8030] [--log capture-log.desktop.json]
 *
 * Runs with Node 24's built-in TypeScript type stripping (erasable syntax only, no tsx needed).
 * For every scene: page.screencast.start({path: <out>/<id>.webm, size: viewport x deviceScaleFactor, fps: 30}),
 * page.screencast.showActions (animated pointer cursor + highlight box + click marker),
 * optional page.screencast.showChapter, then the steps, then page.screencast.stop().
 * Writes <out>/boundingBoxes.json: {sceneId: [{label, t, x, y, w, h}]} where t is seconds since the
 * scene's recording started and x/y/w/h are VIDEO pixels (CSS px x deviceScaleFactor),
 * ready to paste into a Remotion storyboard as zoom keyframes / callout points.
 * With --log, also writes <out>/<log>: per-scene durations, step timestamps, every /api/ response
 * (method, path, status; JSON bodies of story/compile/join calls), DOM text snapshots and failures.
 *
 * API names verified against node_modules/playwright-core/types/types.d.ts (Playwright 1.64.1):
 *   Screencast.start({path,size,fps,quality,onFrame}) -> Promise<Disposable>, .stop(),
 *   .showActions({cursor:'pointer'|'none', duration, position, style:{point,highlight,title}}) -> Promise<Disposable>,
 *   .showChapter(title,{description,duration}), .hideActions(), .showOverlay(html,{duration}).
 */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {chromium, type Locator, type Page} from 'playwright';

type Target =
  | {role: string; name?: string; exact?: boolean}
  | {text: string; exact?: boolean}
  | {label: string}
  | {placeholder: string}
  | {testId: string}
  | {selector: string};

type Step =
  | {goto: string}
  | {click: Target; label?: string}
  | {type: Target; text: string; delay?: number; submit?: boolean; clear?: boolean; label?: string}
  | {press: string}
  | {waitForText: string; timeout?: number; optional?: boolean}
  | {waitForSelector: string; timeout?: number; optional?: boolean}
  | {waitForUrl: string; timeout?: number}
  | {pause: number}
  | {zoomTarget: Target & {as: string}}
  | {snapshot: Target & {as: string}}
  | {apiPost: string; as: string; gotoField?: string}
  | {chapter: string; description?: string; duration?: number}
  | {scroll: number}
  | {scrollTo: Target; block?: 'start' | 'center' | 'end'}
  | {hover: Target; label?: string};

type CaptureScene = {
  id: string;
  chapter?: {title: string; description?: string; duration?: number};
  /** extra still time recorded after the last step (ms, default 600) */
  tailMs?: number;
  /** run before recording starts (not in the clip): goto, or apiPost with gotoField */
  setup?: ({goto: string} | {apiPost: string; as: string; gotoField?: string})[];
  steps: Step[];
};

type CaptureStoryboard = {
  /** base for relative goto URLs; defaults to the storyboard file's folder (file://) */
  baseUrl?: string | null;
  viewport?: {width: number; height: number};
  /** device pixel ratio; the video is recorded at viewport x deviceScaleFactor (default 1) */
  deviceScaleFactor?: number;
  isMobile?: boolean;
  hasTouch?: boolean;
  fps?: number;
  /** default typing delay per character (ms) */
  typeDelay?: number;
  /** ms the cursor hovers on a target before clicking, so the animated pointer arrives first (default 450) */
  hoverMs?: number;
  /** show Playwright's action title overlay ("Click getByRole(...)"); off by default for demo footage */
  actionTitles?: boolean;
  scenes: CaptureScene[];
};

type Box = {label: string; t: number; x: number; y: number; w: number; h: number};

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');

const {values: args} = parseArgs({
  options: {
    storyboard: {type: 'string', short: 's'},
    out: {type: 'string', short: 'o', default: path.join(ROOT, 'footage')},
    headed: {type: 'boolean', default: false},
    only: {type: 'string'},
    'base-url': {type: 'string'},
    log: {type: 'string'},
    /** load cookies (e.g. the tk_sid of a story session) so a later run continues the same session */
    'storage-state': {type: 'string'},
    /** save the context's cookies at the end, so a failed scene can be re-taken in the same session */
    'save-state': {type: 'string'},
  },
});
if (!args.storyboard) {
  console.error('usage: node capture/capture.ts --storyboard <file.json> [--out footage] [--headed] [--only <sceneId>] [--base-url URL] [--log file.json]');
  process.exit(2);
}

const sbPath = path.resolve(args.storyboard);
const sb: CaptureStoryboard = JSON.parse(fs.readFileSync(sbPath, 'utf8'));
const outDir = path.resolve(args.out!);
fs.mkdirSync(outDir, {recursive: true});
const viewport = sb.viewport ?? {width: 1920, height: 1080};
const dsf = sb.deviceScaleFactor ?? 1;
const videoSize = {width: Math.round(viewport.width * dsf), height: Math.round(viewport.height * dsf)};
const fps = sb.fps ?? 30;
const base = args['base-url'] ?? sb.baseUrl ?? pathToFileURL(path.dirname(sbPath) + path.sep).href;

const locate = (page: Page, t: Target): Locator => {
  if ('role' in t) return page.getByRole(t.role as Parameters<Page['getByRole']>[0], {name: t.name, exact: t.exact});
  if ('text' in t) return page.getByText(t.text, {exact: t.exact});
  if ('label' in t) return page.getByLabel(t.label);
  if ('placeholder' in t) return page.getByPlaceholder(t.placeholder);
  if ('testId' in t) return page.getByTestId(t.testId);
  return page.locator(t.selector);
};

const describe = (t: Target): string =>
  'role' in t ? `${t.role}:${t.name ?? ''}` : 'text' in t ? t.text : 'label' in t ? t.label
    : 'placeholder' in t ? t.placeholder : 'testId' in t ? t.testId : t.selector;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------------------------------------
// event log
// ---------------------------------------------------------------------------------------------------------
type SceneLog = {
  id: string;
  file: string;
  started_at: string;
  duration_s?: number;
  ok: boolean;
  error?: string;
  steps: {t: number; step: string}[];
  snapshots: {t: number; as: string; text: string}[];
  api: {t: number; method: string; path: string; status: number; body?: unknown}[];
};
const runLog = {
  storyboard: path.relative(ROOT, sbPath),
  base_url: base,
  viewport,
  device_scale_factor: dsf,
  video_size: videoSize,
  fps,
  started_at: new Date().toISOString(),
  finished_at: '' as string,
  scenes: [] as SceneLog[],
};
let current: SceneLog | null = null;
let sceneT0 = Date.now();
const KEEP_BODY = /\/api\/(judge|circles\/compile|circles\/[^/]+\/charter|join)/;
const writeLog = () => {
  if (!args.log) return;
  runLog.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(outDir, args.log), JSON.stringify(runLog, null, 2));
};

// Headless Chromium's screencast frames are in CSS pixels unless the device scale is forced at launch;
// without this flag a deviceScaleFactor 2 take is a 1x picture padded into the top-left of a 2x canvas.
const browser = await chromium.launch({
  headless: !args.headed,
  args: dsf !== 1 ? [`--force-device-scale-factor=${dsf}`] : [],
});
const context = await browser.newContext({
  viewport,
  deviceScaleFactor: dsf,
  isMobile: sb.isMobile ?? false,
  hasTouch: sb.hasTouch ?? false,
  colorScheme: 'light',
  ...(args['storage-state'] ? {storageState: path.resolve(args['storage-state'])} : {}),
});
const page = await context.newPage();

page.on('response', async (resp) => {
  try {
    const u = new URL(resp.url());
    if (!u.pathname.startsWith('/api/')) return;
    const rec: SceneLog['api'][number] = {
      t: +((Date.now() - sceneT0) / 1000).toFixed(3),
      method: resp.request().method(),
      path: u.pathname + u.search,
      status: resp.status(),
    };
    if (KEEP_BODY.test(u.pathname) && (resp.headers()['content-type'] ?? '').includes('json')) {
      const txt = await resp.text();
      rec.body = txt.length > 60_000 ? txt.slice(0, 60_000) + '...[truncated]' : JSON.parse(txt);
    }
    (current?.api ?? (runLog as any).api_outside_scenes ?? ((runLog as any).api_outside_scenes = [])).push(rec);
  } catch {
    /* page closed / body unavailable */
  }
});

const allBoxes: Record<string, Box[]> = {};
let failed = false;

for (const scene of sb.scenes) {
  if (args.only && scene.id !== args.only) continue;
  const file = path.join(outDir, `${scene.id}.webm`);
  const boxes: Box[] = [];
  allBoxes[scene.id] = boxes;
  const slog: SceneLog = {id: scene.id, file: path.relative(ROOT, file), started_at: new Date().toISOString(), ok: false, steps: [], snapshots: [], api: []};
  runLog.scenes.push(slog);
  current = slog;
  sceneT0 = Date.now();

  // `setup` steps (goto / apiPost) run BEFORE recording and are not part of the clip.
  for (const s of scene.setup ?? []) {
    if ('goto' in s) {
      await page.goto(new URL(s.goto, base).href, {waitUntil: 'networkidle'});
    } else if ('apiPost' in s) {
      const resp = await page.request.post(new URL(s.apiPost, base).href, {data: {}});
      const body = await resp.json().catch(() => null);
      slog.snapshots.push({t: 0, as: s.as, text: JSON.stringify({status: resp.status(), body})});
      if (!resp.ok()) throw new Error(`setup POST ${s.apiPost} -> ${resp.status()}`);
      if (s.gotoField) {
        const target = s.gotoField.split('.').reduce((o: any, k) => o?.[k], body);
        await page.goto(new URL(String(target), base).href, {waitUntil: 'networkidle'});
      }
    }
  }
  // A scene that starts with goto navigates BEFORE recording so the clip does not open on a blank page.
  const steps = [...scene.steps];
  const first = steps[0];
  if (first && 'goto' in first) {
    steps.shift();
    await page.goto(new URL(first.goto, base).href, {waitUntil: 'networkidle'});
  }

  const actions = await page.screencast.showActions({
    cursor: 'pointer',
    duration: 700,
    position: 'top-right',
    style: {
      point: 'width: 26px; height: 26px; border-radius: 50%; border: 4px solid #f5a623; background: rgba(245,166,35,.25)',
      highlight: 'outline: 4px solid #0070e0; border-radius: 8px; background: rgba(0,112,224,.10)',
      title: sb.actionTitles
        ? 'font: 600 22px Inter, system-ui, sans-serif; background: #0b1f3a; color: #fff; padding: 8px 14px; border-radius: 10px'
        : 'display: none',
    },
  });
  await page.screencast.start({path: file, size: videoSize, fps});
  const t0 = Date.now();
  sceneT0 = t0;
  slog.started_at = new Date(t0).toISOString();
  const now = () => (Date.now() - t0) / 1000;
  const mark = (s: string) => slog.steps.push({t: +now().toFixed(3), step: s});
  const record = async (label: string, loc: Locator, timeout = 30_000) => {
    const b = await loc.first().boundingBox({timeout});
    if (b) boxes.push({label, t: +now().toFixed(3), x: Math.round(b.x * dsf), y: Math.round(b.y * dsf), w: Math.round(b.width * dsf), h: Math.round(b.height * dsf)});
  };

  // Hold the recording until the chapter overlay has finished (sleeps only the remainder if showChapter blocks).
  const chapter = async (title: string, description?: string, duration = 1800) => {
    const c0 = Date.now();
    await page.screencast.showChapter(title, {description, duration});
    await sleep(Math.max(0, duration - (Date.now() - c0)) + 200);
  };
  // Hover first so the animated pointer travels to the target before the click marker fires.
  const hoverClick = async (loc: Locator) => {
    await loc.hover();
    await sleep(sb.hoverMs ?? 450);
    await loc.click();
  };

  try {
    if (scene.chapter) await chapter(scene.chapter.title, scene.chapter.description, scene.chapter.duration);

    for (const step of steps) {
      if ('goto' in step) {
        mark(`goto ${step.goto}`);
        await page.goto(new URL(step.goto, base).href, {waitUntil: 'networkidle'});
      } else if ('click' in step) {
        const loc = locate(page, step.click).first();
        mark(`click ${step.label ?? describe(step.click)}`);
        await record(step.label ?? `click:${describe(step.click)}`, loc);
        await hoverClick(loc);
      } else if ('type' in step) {
        const loc = locate(page, step.type).first();
        mark(`type ${step.label ?? describe(step.type)}`);
        await record(step.label ?? `type:${describe(step.type)}`, loc);
        await hoverClick(loc);
        if (step.clear) await loc.fill('');
        await loc.pressSequentially(step.text, {delay: step.delay ?? sb.typeDelay ?? 45});
        if (step.submit) await loc.press('Enter');
      } else if ('press' in step) {
        await page.keyboard.press(step.press);
      } else if ('waitForText' in step) {
        try {
          await page.getByText(step.waitForText).first().waitFor({state: 'visible', timeout: step.timeout ?? 60_000});
          mark(`seen "${step.waitForText}"`);
        } catch (e) {
          if (!step.optional) throw e;
          mark(`NOT seen (optional) "${step.waitForText}"`);
        }
      } else if ('waitForSelector' in step) {
        try {
          await page.locator(step.waitForSelector).first().waitFor({state: 'visible', timeout: step.timeout ?? 60_000});
          mark(`seen ${step.waitForSelector}`);
        } catch (e) {
          if (!step.optional) throw e;
          mark(`NOT seen (optional) ${step.waitForSelector}`);
        }
      } else if ('waitForUrl' in step) {
        await page.waitForURL(new RegExp(step.waitForUrl), {timeout: step.timeout ?? 60_000, waitUntil: 'domcontentloaded'});
        mark(`url ${page.url().split('?')[0]}`);
      } else if ('pause' in step) {
        await sleep(step.pause);
      } else if ('zoomTarget' in step) {
        // A missing zoom target never fails the take: it only loses a box.
        const {as, ...target} = step.zoomTarget;
        try {
          await record(as, locate(page, target as Target), 3_000);
        } catch {
          mark(`zoomTarget ${as} not found`);
          console.warn(`  ${scene.id}: zoom target '${as}' not found`);
        }
      } else if ('snapshot' in step) {
        const {as, ...target} = step.snapshot;
        try {
          const text = await locate(page, target as Target).first().innerText({timeout: 3_000});
          slog.snapshots.push({t: +now().toFixed(3), as, text});
        } catch {
          mark(`snapshot ${as} not found`);
        }
      } else if ('apiPost' in step) {
        const resp = await page.request.post(new URL(step.apiPost, base).href, {data: {}});
        const body = await resp.json().catch(() => null);
        slog.snapshots.push({t: +now().toFixed(3), as: step.as, text: JSON.stringify({status: resp.status(), body})});
        if (!resp.ok()) throw new Error(`POST ${step.apiPost} -> ${resp.status()}`);
        if (step.gotoField) {
          const target = step.gotoField.split('.').reduce((o: any, k) => o?.[k], body);
          mark(`goto ${target}`);
          await page.goto(new URL(String(target), base).href, {waitUntil: 'networkidle'});
        }
      } else if ('chapter' in step) {
        await chapter(step.chapter, step.description, step.duration);
      } else if ('scroll' in step) {
        await page.mouse.wheel(0, step.scroll);
        await sleep(400);
      } else if ('scrollTo' in step) {
        // Smoothly bring a target into view (the recording shows the scroll). Used for panels below the fold.
        const loc = locate(page, step.scrollTo).first();
        await loc.evaluate((el, block) => el.scrollIntoView({behavior: 'smooth', block}), step.block ?? 'center');
        await sleep(900);
      } else if ('hover' in step) {
        const loc = locate(page, step.hover).first();
        mark(`hover ${step.label ?? describe(step.hover)}`);
        if (step.label) await record(step.label, loc);
        await loc.hover();
      }
    }
    await sleep(scene.tailMs ?? 600);
    slog.ok = true;
  } catch (e) {
    slog.error = String((e as Error)?.message ?? e).split('\n').slice(0, 6).join('\n');
    failed = true;
    try {
      await page.screenshot({path: path.join(outDir, `${scene.id}.FAILED.png`)});
    } catch {}
  }

  await page.screencast.stop();
  await actions[Symbol.asyncDispose]();
  slog.duration_s = +now().toFixed(2);
  console.log(`${slog.ok ? 'recorded' : 'FAILED  '} ${path.relative(ROOT, file)} (${now().toFixed(1)}s, ${boxes.length} boxes)${slog.error ? '\n  ' + slog.error : ''}`);
  writeLog();
  if (failed) break; // later scenes depend on this one's state
}
current = null;

const boxesFile = path.join(outDir, 'boundingBoxes.json');
// merge with existing file so `--only` re-takes / separate runs keep the other scenes' boxes
let merged: Record<string, Box[]> = {};
try { merged = JSON.parse(fs.readFileSync(boxesFile, 'utf8')); } catch {}
fs.writeFileSync(boxesFile, JSON.stringify({...merged, ...allBoxes}, null, 2));
console.log(`wrote ${path.relative(ROOT, boxesFile)}`);
writeLog();
if (args['save-state']) await context.storageState({path: path.resolve(args['save-state'])});

await context.close();
await browser.close();
process.exit(failed ? 1 : 0);
