/**
 * Supplementary captures for the v2 film, taken from the persisted DB of the recorded Judge Mode run.
 *
 *   node capture/extra.mjs --base http://localhost:8035 --state <tkcap-state.json> --replay <replay.json> --out public/footage
 *
 * The server must be started on a COPY of the run's SQLite files (TK_LLM_PROVIDER=none, so no model call is made).
 * - x1-trace.webm   the app's own "Treasurer trace" panel. The trace panel is fed only by SSE events, so a stub
 *                   EventSource re-delivers the trace events that the real run recorded (GET /api/replay, step
 *                   run_round3) one by one. Nothing is edited or invented; the text is the run's own.
 * - x2-receipt.webm the app's own "Payout receipt" panel as it loads from the database (round 4, status SUCCESS).
 * Viewport 1200x1000 CSS px at device scale 1.6 -> 1920x1600 video, so zooms stay sharp.
 */
import fs from 'node:fs';
import path from 'node:path';
import {chromium} from 'playwright';

const arg = (k, d) => {
  const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d;
};
const base = arg('base', 'http://localhost:8035');
const state = JSON.parse(fs.readFileSync(arg('state'), 'utf8'));
const replay = JSON.parse(fs.readFileSync(arg('replay'), 'utf8'));
const out = arg('out', 'public/footage');
const only = arg('only', '');
const DSF = 1.6;
const VW = 1200;
const VH = 1000;

const stub = `
(() => {
  class FakeES {
    constructor(url) {
      this.url = url; this.readyState = 0; this.onopen = null; this.onmessage = null; this.onerror = null; this._l = {};
      window.__es = this;
      setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(new Event('open')); this._emit({type: 'hello', ts: new Date().toISOString(), sid: 'x'}); }, 40);
    }
    addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); }
    close() { this.readyState = 2; }
    _emit(o) { const ev = new MessageEvent('message', {data: JSON.stringify(o)}); this.onmessage && this.onmessage(ev); (this._l['message'] || []).forEach((f) => f(ev)); }
  }
  window.EventSource = FakeES;
  window.__emit = (o) => window.__es && window.__es._emit(o);
})();
`;

const browser = await chromium.launch({args: [`--force-device-scale-factor=${DSF}`]});
const mk = async () => {
  const ctx = await browser.newContext({
    viewport: {width: VW, height: VH},
    deviceScaleFactor: DSF,
    colorScheme: 'light',
    storageState: {cookies: state.cookies.filter((c) => c.domain === 'localhost'), origins: []},
  });
  await ctx.addInitScript(stub);
  return ctx;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const size = {width: Math.round(VW * DSF), height: Math.round(VH * DSF)};

async function record(page, file, body) {
  await page.screencast.start({path: file, size, fps: 30});
  const t0 = Date.now();
  await body(() => (Date.now() - t0) / 1000);
  await page.screencast.stop();
}

const log = {};

if (!only || only === 'trace') {
  const ctx = await mk();
  const page = await ctx.newPage();
  await page.goto(base + '/', {waitUntil: 'networkidle'});
  await sleep(2500);
  const trace = page.locator("section[aria-label='Agent trace']");
  await trace.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, 330));
  await sleep(700);
  const step = replay.steps.find((s) => s.step === 'run_round3');
  const evs = step.events.filter((e) => e.type === 'trace' && e.kind !== 'text');
  const gaps = (e, i) => (e.kind === 'system' ? 650 : e.kind === 'tool_call' ? 380 : 520);
  await record(page, path.join(out, 'x1-trace.webm'), async (now) => {
    await sleep(900);
    log.trace = [];
    for (let i = 0; i < evs.length; i++) {
      await page.evaluate((e) => window.__emit(e), evs[i]);
      log.trace.push({t: +now().toFixed(2), kind: evs[i].kind, tool: evs[i].tool ?? null});
      await sleep(gaps(evs[i], i));
    }
    await sleep(2600);
    const b = await trace.boundingBox();
    log.trace_box = b && {x: Math.round(b.x * DSF), y: Math.round(b.y * DSF), w: Math.round(b.width * DSF), h: Math.round(b.height * DSF)};
  });
  await ctx.close();
}

if (!only || only === 'receipt') {
  const ctx = await mk();
  const page = await ctx.newPage();
  await page.goto(base + '/', {waitUntil: 'networkidle'});
  await sleep(3000);
  const rc = page.locator("section[aria-label='Payout receipt']");
  await rc.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, 260));
  await sleep(700);
  await record(page, path.join(out, 'x2-receipt.webm'), async (now) => {
    await sleep(6500);
    const b = await rc.boundingBox();
    log.receipt_box = b && {x: Math.round(b.x * DSF), y: Math.round(b.y * DSF), w: Math.round(b.width * DSF), h: Math.round(b.height * DSF)};
    log.receipt_text = (await rc.innerText()).replace(/\n+/g, ' | ');
    const ids = await rc.locator('.tk-mono, code, [class*=mono]').evaluateAll((els) => els.map((e) => {const r = e.getBoundingClientRect(); return {t: e.textContent, x: r.x, y: r.y, w: r.width, h: r.height};}));
    log.receipt_ids = ids;
  });
  await page.screenshot({path: path.join(out, 'x2-receipt.png')});
  await ctx.close();
}

await browser.close();
fs.writeFileSync(path.join(out, 'extra-log.json'), JSON.stringify(log, null, 2));
console.log(JSON.stringify(log, null, 1));
