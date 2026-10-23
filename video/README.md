# Turnkeeper demo video toolchain

Pipeline: **Playwright screencast** of the real running app -> **Kokoro TTS** voiceover ->
**Remotion** composition (title cards, zoom/pan, callouts, captions, transitions, ducked music) ->
**ffmpeg loudnorm** to -14 LUFS.

Everything lives in `video/` and is independent of `app/` and `web/`.

```
video/
  package.json            pinned: remotion/@remotion/* 4.0.538, react 19.3.1, playwright 1.64.1, typescript 6.0.3
  remotion.config.ts      Remotion CLI config (public dir, h264/yuv420p, crf 18, swangle)
  remotion/src/           Root.tsx (composition "Main", 1920x1080@30), Main.tsx, components/
  remotion/storyboard.test.json   3-scene smoke-test storyboard (default props)
  capture/capture.ts      Playwright screencast recorder (one webm per scene + boundingBoxes.json)
  capture/storyboard.test.json, capture/test/test-page.html   capture smoke test
  tts/say.py              Kokoro (English) + edge-tts (Hindi) voiceover -> WAVs + durations.json
  public/                 Remotion public dir: fonts/ (Inter, bundled), footage/, audio/
  footage -> public/footage, audio -> public/audio   (directory junctions, see "Media layout")
  scripts/                link-public.mjs, master.mjs, setup-node-x64.ps1, env-x64.ps1/.sh
```

## Important: this laptop is Windows on ARM (Snapdragon X)

The system Node is **arm64**, and Remotion 4.0.538 ships no `win32-arm64` compositor
(`Unsupported architecture on Windows: arm64`). All Node work in `video/` therefore runs on a
portable **x64 Node 24.21.0** in `video/.tools/node-x64` (Prism emulation; works fine).
`node_modules` was installed with that x64 Node, so it contains the x64 esbuild/compositor binaries.

```powershell
cd video
.\scripts\setup-node-x64.ps1     # only if .tools/node-x64 is missing (verifies SHA-256)
. .\scripts\env-x64.ps1          # every new shell: put x64 node first on PATH  (Git Bash: source scripts/env-x64.sh)
npm install --no-audit --no-fund # if node_modules is missing; must be run with x64 node
npx playwright install chromium
npm run link                     # creates the footage/ and audio/ junctions
```

Python: the requested `py -3.11` venv (`tts/.venv`, x64 Python, emulated) works, but a native
ARM64 venv (`tts/.venv-arm64`, `py -3.12-arm64`) is ~2x faster for Kokoro. Both have the same packages
(`tts/requirements.txt`). Recreate with:

```powershell
py -3.11 -m venv tts/.venv;              tts/.venv/Scripts/python -m pip install -r tts/requirements.txt
py -3.12-arm64 -m venv tts/.venv-arm64;  tts/.venv-arm64/Scripts/python -m pip install -r tts/requirements.txt
```

Kokoro model files (gitignored, `tts/models/`), from the official kokoro-onnx release
<https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0>:

```bash
curl -L -o tts/models/kokoro-v1.0.int8.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl -L -o tts/models/voices-v1.0.bin       https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
# optional fp32 (325 MB): kokoro-v1.0.onnx  -> use with  say.py --model tts/models/kokoro-v1.0.onnx
```

Measured on this CPU (RTF = compute seconds / audio seconds, model load excluded):

| interpreter | int8 | fp32 |
|---|---|---|
| ARM64 Python 3.12 (`.venv-arm64`) | **1.1** | 1.5 |
| x64 Python 3.11 (`.venv`, emulated) | 2.1 | 1.7 |

So a 3-minute script takes ~3-4 minutes to synthesise. Hindi via edge-tts needs internet (~1-3 s per line).

## End-to-end

### 1. Capture footage (app must be running; do it on the real Turnkeeper UI)

Write `capture/storyboard.json` (see `capture/storyboard.test.json`):

```json
{
  "baseUrl": "http://localhost:8000",
  "viewport": {"width": 1920, "height": 1080}, "fps": 30, "typeDelay": 40,
  "hoverMs": 450, "actionTitles": false,
  "scenes": [
    {"id": "scene-01", "chapter": {"title": "Ask", "description": "optional", "duration": 1500},
     "tailMs": 600,
     "steps": [
       {"goto": "/"},
       {"type": {"role": "textbox", "name": "Message"}, "text": "Refund order 1042", "submit": true},
       {"click": {"role": "button", "name": "Send"}},
       {"click": {"text": "Approve"}, "label": "approve-btn"},
       {"waitForText": "Approved", "timeout": 60000},
       {"zoomTarget": {"text": "Approved", "as": "verdict"}},
       {"pause": 1200}
     ]}
  ]
}
```

Targets: `{role,name,exact}` | `{text,exact}` | `{label}` | `{placeholder}` | `{testId}` | `{selector}`.
Steps: `goto`, `click`, `type` (+`submit`,`clear`,`delay`), `press`, `waitForText`, `waitForSelector`,
`pause` (ms), `zoomTarget` (records a box only), `chapter`, `scroll`.

```bash
npm run capture                        # all scenes -> footage/<id>.webm + footage/boundingBoxes.json
node capture/capture.ts --storyboard capture/storyboard.json --only scene-03 --headed   # re-take one scene
npm run capture:test                   # smoke test against capture/test/test-page.html
```

- A leading `goto` runs *before* recording starts, so clips never open on a blank page.
- `page.screencast.showActions({cursor: 'pointer', style: {point, highlight, title}})` gives the
  animated cursor, orange click ring and blue highlight box; action titles are hidden unless
  `actionTitles: true`. Clicks hover first (`hoverMs`) so the cursor arrives before the click marker.
- `boundingBoxes.json`: `{sceneId: [{label, t, x, y, w, h}]}`; `t` = seconds since that scene's
  recording started (accurate to ~0.1-0.2 s), boxes in video pixels. Every click/type target is
  recorded automatically, plus explicit `zoomTarget`s. Use them as Remotion zoom keyframes/callouts.
- Output is VP9 webm, 1920x1080, 30 fps. API names were checked against
  `node_modules/playwright-core/types/types.d.ts` (1.64.1): `screencast.start({path,size,fps,quality})`,
  `stop()`, `showActions(...)` / `hideActions()`, `showChapter(title,{description,duration})`, `showOverlay`.

### 2. Voiceover

`tts/script.json`: `[{"id": "s01", "text": "...", "voice": "af_heart"}, {"id": "s05", "text": "...", "voice": "bf_emma"}, {"id": "s06", "text": "हिंदी ...", "lang": "hi"}]`

```bash
tts/.venv-arm64/Scripts/python.exe tts/say.py --in tts/script.json --out audio/vo
# -> audio/vo/<id>.wav (24 kHz mono) + audio/vo/durations.json {"s01": 6.02, ...}
```

Voices: `af_heart` (US, default), `bf_emma` (UK; `b*` voices automatically use `en-gb`).
`lang: "hi"` lines go to edge-tts `hi-IN-SwaraNeural` (online), mp3 -> wav via ffmpeg. Optional `speed`.

### 3. Storyboard + render

`remotion/storyboard.json` (shape in `remotion/src/types.ts`; example `remotion/storyboard.test.json`):

```json
{
  "audioDir": "audio/vo", "padSec": 0.6, "transitionSec": 0.5,
  "music": {"src": "audio/music.mp3", "volume": 0.2, "duckedVolume": 0.04},
  "scenes": [
    {"id": "intro", "type": "title", "title": "Turnkeeper", "subtitle": "...", "voiceId": "s01", "transition": "fade"},
    {"id": "ask", "type": "clip", "src": "footage/scene-01.webm", "startFromSec": 0.5, "voiceId": "s02",
     "caption": "Text burned in at the bottom, max two lines.",
     "zoom": [{"t": 0, "x": 0, "y": 0, "w": 1920, "h": 1080}, {"t": 2.0, "x": 80, "y": 180, "w": 960, "h": 540}],
     "callouts": [{"t": 2.4, "dur": 2, "x": 1093, "y": 293, "label": "Verdict", "side": "left"}],
     "transition": "slide"},
    {"id": "wrap", "type": "slide", "title": "Why it matters", "bullets": ["...", "..."], "voiceId": "s03"}
  ]
}
```

- Scene length = `durationSec` if set, else voice length (from `durations.json`) + `voiceDelaySec` (0.3) + `padSec`.
- Zoom keyframes are rects in source pixels at `t` seconds (scene-relative), eased in/out, clamped to
  the frame; callout points are in source pixels and follow the zoom.
- Captions: words are timed proportionally across the voice window and paged with
  `@remotion/captions` `createTikTokStyleCaptions`; the current word is tinted `#f5a623`.
- Music is ducked from `volume` to `duckedVolume` while any voice line plays.
- Brand: navy `#0b1f3a`, blue `#0070e0`, accent `#f5a623`, white; Inter 400/600/800 from `public/fonts` (no CDN).

```bash
npm run studio          # live preview
npm run render          # -> out/main.mp4  (uses remotion/storyboard.json as props)
npm run render:test     # -> out/test.mp4  (6 s smoke test, default props)
```

### 4. Master

```bash
npm run master          # two-pass loudnorm I=-14 LUFS, TP=-1.5 dBTP, video copied -> out/final.mp4
```

## Media layout

Remotion copies its public dir into the bundle and tries to recreate symlinks as real symlinks,
which fails on Windows without admin/Developer Mode (`EPERM: symlink`). So media files physically
live in `public/footage` and `public/audio`, and `video/footage` / `video/audio` are directory
junctions pointing there (`npm run link`). Reference media in storyboards as `footage/...` and `audio/...`.

## Smoke-test results (this machine)

- `npm run render:test`: 6.0 s 1920x1080 h264 + aac in ~26 s warm (~60 s first run incl. bundling).
- `npm run capture:test`: two scenes (11.4 s + 7.6 s VP9 webm) in ~19 s, headless.
- `say.py` on 3 sample lines: see the RTF table above.
