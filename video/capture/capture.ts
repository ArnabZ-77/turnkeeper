/**
 * Playwright screencast capture for the Turnkeeper demo.
 *
 *   node capture/capture.ts --storyboard capture/storyboard.test.json [--out footage] [--headed] [--only scene-01]
 *
 * Runs with Node 24's built-in TypeScript type stripping (erasable syntax only, no tsx needed).
 * For every scene: page.screencast.start({path: <out>/<id>.webm, size: 1920x1080, fps: 30}),
 * page.screencast.showActions (animated pointer cursor + highlight box + click marker),
 * optional page.screencast.showChapter, then the steps, then page.screencast.stop().
 * Writes <out>/boundingBoxes.json: {sceneId: [{label, t, x, y, w, h}]} where t is seconds since the
 * scene's recording started and x/y/w/h are video pixels (viewport 1920x1080, deviceScaleFactor 1),
 * ready to paste into a Remotion storyboard as zoom keyframes / callout points.
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
  | {waitForText: string; timeout?: number}
  | {waitForSelector: string; timeout?: number}
  | {pause: number}
  | {zoomTarget: Target & {as: string}}
  | {chapter: string; description?: string; duration?: number}
  | {scroll: number};

type CaptureScene = {
  id: string;
  chapter?: {title: string; description?: string; duration?: number};
  /** extra still time recorded after the last step (ms, default 600) */
  tailMs?: number;
  steps: Step[];
};

type CaptureStoryboard = {
  /** base for relative goto URLs; defaults to the storyboard file's folder (file://) */
  baseUrl?: string | null;
  viewport?: {width: number; height: number};
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
  },
});
if (!args.storyboard) {
  console.error('usage: node capture/capture.ts --storyboard <file.json> [--out footage] [--headed] [--only <sceneId>]');
  process.exit(2);
}

const sbPath = path.resolve(args.storyboard);
const sb: CaptureStoryboard = JSON.parse(fs.readFileSync(sbPath, 'utf8'));
const outDir = path.resolve(args.out!);
fs.mkdirSync(outDir, {recursive: true});
const viewport = sb.viewport ?? {width: 1920, height: 1080};
const fps = sb.fps ?? 30;
const base = sb.baseUrl ?? pathToFileURL(path.dirname(sbPath) + path.sep).href;

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

const browser = await chromium.launch({headless: !args.headed});
const context = await browser.newContext({viewport, deviceScaleFactor: 1, colorScheme: 'light'});
const page = await context.newPage();

const allBoxes: Record<string, Box[]> = {};

for (const scene of sb.scenes) {
  if (args.only && scene.id !== args.only) continue;
  const file = path.join(outDir, `${scene.id}.webm`);
  const boxes: Box[] = [];
  allBoxes[scene.id] = boxes;

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
  await page.screencast.start({path: file, size: viewport, fps});
  const t0 = Date.now();
  const now = () => (Date.now() - t0) / 1000;
  const record = async (label: string, loc: Locator) => {
    const b = await loc.first().boundingBox();
    if (b) boxes.push({label, t: +now().toFixed(3), x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)});
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

  if (scene.chapter) await chapter(scene.chapter.title, scene.chapter.description, scene.chapter.duration);

  for (const step of steps) {
    if ('goto' in step) {
      await page.goto(new URL(step.goto, base).href, {waitUntil: 'networkidle'});
    } else if ('click' in step) {
      const loc = locate(page, step.click).first();
      await record(step.label ?? `click:${describe(step.click)}`, loc);
      await hoverClick(loc);
    } else if ('type' in step) {
      const loc = locate(page, step.type).first();
      await record(step.label ?? `type:${describe(step.type)}`, loc);
      await hoverClick(loc);
      if (step.clear) await loc.fill('');
      await loc.pressSequentially(step.text, {delay: step.delay ?? sb.typeDelay ?? 45});
      if (step.submit) await loc.press('Enter');
    } else if ('press' in step) {
      await page.keyboard.press(step.press);
    } else if ('waitForText' in step) {
      await page.getByText(step.waitForText).first().waitFor({state: 'visible', timeout: step.timeout ?? 60_000});
    } else if ('waitForSelector' in step) {
      await page.locator(step.waitForSelector).first().waitFor({state: 'visible', timeout: step.timeout ?? 60_000});
    } else if ('pause' in step) {
      await sleep(step.pause);
    } else if ('zoomTarget' in step) {
      const {as, ...target} = step.zoomTarget;
      await record(as, locate(page, target as Target));
    } else if ('chapter' in step) {
      await chapter(step.chapter, step.description, step.duration);
    } else if ('scroll' in step) {
      await page.mouse.wheel(0, step.scroll);
      await sleep(400);
    }
  }

  await sleep(scene.tailMs ?? 600);
  await page.screencast.stop();
  await actions[Symbol.asyncDispose]();
  console.log(`recorded ${path.relative(ROOT, file)} (${now().toFixed(1)}s, ${boxes.length} boxes)`);
}

const boxesFile = path.join(outDir, 'boundingBoxes.json');
// merge with existing file so `--only` re-takes keep the other scenes' boxes
let merged: Record<string, Box[]> = {};
try { merged = JSON.parse(fs.readFileSync(boxesFile, 'utf8')); } catch {}
fs.writeFileSync(boxesFile, JSON.stringify({...merged, ...allBoxes}, null, 2));
console.log(`wrote ${path.relative(ROOT, boxesFile)}`);

await context.close();
await browser.close();
