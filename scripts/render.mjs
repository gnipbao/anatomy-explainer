#!/usr/bin/env node
// Node 22 built-ins only. Each frame is rendered at frame / fps, never recorded in real time.
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { access, link, lstat, mkdir, mkdtemp, readFile, realpath, rename, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { basename, dirname, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const USAGE = `Deterministic anatomy video renderer (Node 22+, Chrome, ffmpeg, ffprobe).
  node scripts/render.mjs --project PROJECT [--out FILE] [--force]
  node scripts/render.mjs --project PROJECT --frame NUMBER --still FILE [--force]
PROJECT contains index.html, job.json, timing.json and (for video) narration-full.wav.
Set CHROME_PATH, FFMPEG_PATH or FFPROBE_PATH to override executable discovery.
Relative output paths are resolved from the current working directory.
Assets must be local to PROJECT. renderFrame(t) receives seconds and may return a Promise.`;

const delay = (ms) => new Promise((done) => setTimeout(done, ms));
const exists = async (path) => { try { await access(path); return true; } catch { return false; } };
const inside = (root, path) => { const rel = relative(root, path); return !rel || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`)); };
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.mp4': 'video/mp4' };
const groupedChildren = new WeakSet();
function killChild(child) {
  try {
    if (groupedChildren.has(child) && child.pid) process.kill(-child.pid, 'SIGKILL');
    else child.kill('SIGKILL');
  } catch { /* already exited */ }
}

export function parseArgs(argv) {
  const options = { force: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--force') options.force = true;
    else if (flag === '--help' || flag === '-h') options.help = true;
    else if (['--project', '--out', '--frame', '--still'].includes(flag)) {
      const value = argv[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for ${flag}.`);
      const key = flag.slice(2);
      if (options[key] !== undefined) throw new Error(`Duplicate option ${flag}.`);
      options[key] = key === 'frame' ? Number(value) : value;
    } else throw new Error('Unknown argument. Use --help for supported flags.');
  }
  if (options.help) return options;
  if (!options.project) throw new Error('--project is required.');
  if (options.frame !== undefined && (!Number.isSafeInteger(options.frame) || options.frame < 0)) throw new Error('--frame must be a nonnegative integer.');
  if ((options.frame !== undefined) !== (options.still !== undefined)) throw new Error('--frame and --still must be used together.');
  if (options.frame !== undefined && options.out) throw new Error('--out is for full video rendering; use --still for a frame.');
  if (options.still && extname(options.still).toLowerCase() !== '.png') throw new Error('--still must end with .png.');
  if (options.out && extname(options.out).toLowerCase() !== '.mp4') throw new Error('--out must end with .mp4.');
  return options;
}

export function validateConfig(job, timing) {
  const { width, height, fps } = job?.video ?? {};
  for (const [key, value] of Object.entries({ width, height })) {
    if (!Number.isSafeInteger(value) || value < 2 || value > 8192 || value % 2) throw new Error(`job.video.${key} must be an even integer from 2 to 8192 for H.264 yuv420p.`);
  }
  if (!Number.isFinite(fps) || fps <= 0 || fps > 120) throw new Error('job.video.fps must be greater than 0 and at most 120.');
  if (!Number.isFinite(timing?.duration) || timing.duration <= 0 || timing.duration > 10800) throw new Error('timing.duration must be seconds greater than 0 and at most 10800.');
  if (timing.scenes !== undefined && !Array.isArray(timing.scenes)) throw new Error('timing.scenes must be an array when supplied.');
  const frameCount = Math.ceil(timing.duration * fps);
  return { width, height, fps, frameCount, duration: frameCount / fps, requestedDuration: timing.duration };
}

export async function verifyBuildManifest(project, snapshots = new Map()) {
  const manifestPath = join(project, 'build-manifest.json');
  try { await lstat(manifestPath); } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  const root = await realpath(project);
  if (!inside(root, await realpath(manifestPath))) throw new Error('Build manifest must remain inside the project directory.');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  const names = ['data.json', 'job.json', 'timing.json'];
  if (manifest?.schema_version !== 1 || !manifest.inputs || typeof manifest.inputs !== 'object' || Array.isArray(manifest.inputs) || Object.keys(manifest.inputs).sort().join('\0') !== names.join('\0')) throw new Error('Invalid build-manifest.json schema or input set. Rebuild the project.');
  for (const name of names) {
    const hash = manifest.inputs[name];
    if (typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash)) throw new Error('Build manifest requires complete SHA-256 input hashes. Rebuild the project.');
    const path = join(root, name);
    if (!inside(root, await realpath(path))) throw new Error('Manifest inputs must remain inside the project directory.');
    const bytes = snapshots.get(name) ?? await readFile(path);
    if (createHash('sha256').update(bytes).digest('hex') !== hash) throw new Error('Project inputs changed since the build. Rebuild before rendering.');
  }
  return true;
}

export function validateNarrationProbe(probe, expectedDuration) {
  const audio = probe?.streams?.find((stream) => stream.codec_type === 'audio');
  const streamDuration = Number(audio?.duration);
  const duration = Number.isFinite(streamDuration) && streamDuration > 0 ? streamDuration : Number(probe?.format?.duration);
  if (!audio || !Number.isFinite(duration) || duration <= 0) throw new Error('narration-full.wav must contain an audio stream with a measurable duration.');
  if (Math.abs(duration - expectedDuration) > Math.max(0.01, 1 / 48000) + Number.EPSILON) throw new Error('Narration duration does not match timing.duration within 0.01 seconds. Regenerate narration and timing before rendering.');
  return duration;
}

// Resolve the raw HTTP request target before URL normalization can erase traversal segments.
// realpath closes symlink escapes as well as lexical ../ escapes.
export async function resolveRequestPath(project, requestTarget) {
  const root = await realpath(project);
  const raw = String(requestTarget ?? '').split(/[?#]/, 1)[0];
  if (!raw.startsWith('/') || raw.startsWith('//')) throw new Error('Forbidden request target.');
  let decoded;
  try { decoded = decodeURIComponent(raw); } catch { throw new Error('Invalid URL encoding.'); }
  if (decoded.includes('\0') || decoded.includes('\\') || decoded.split('/').includes('..')) throw new Error('Forbidden request path.');
  const candidate = resolve(root, `.${decoded === '/' ? '/index.html' : decoded}`);
  if (!inside(root, candidate)) throw new Error('Forbidden request path.');
  const actual = await realpath(candidate);
  if (!inside(root, actual) || !(await stat(actual)).isFile()) throw new Error('Forbidden project resource.');
  return actual;
}

export async function assertOutputSafe(output, inputs, force = false) {
  const target = resolve(output);
  const canonical = await exists(target) ? await realpath(target) : join(await realpath(dirname(target)), basename(target));
  for (const input of inputs) {
    const known = await exists(input) ? await realpath(input) : resolve(input);
    if (canonical === known) throw new Error('Output would overwrite a project input. Choose a different output path.');
  }
  if (await exists(target) && !force) throw new Error('Output already exists. Use --force to replace a generated output.');
  if (await exists(target) && !(await stat(target)).isFile()) throw new Error('Output must be a regular file.');
  return target;
}

async function serveProject(project, served, onFailure) {
  const server = createServer(async (req, res) => {
    // Chrome's optional favicon request is the only missing resource that can be ignored.
    const optionalFavicon = String(req.url ?? '').split(/[?#]/, 1)[0] === '/favicon.ico';
    const failed = () => { if (!optionalFavicon) onFailure(); };
    if (!['GET', 'HEAD'].includes(req.method)) { failed(); res.writeHead(405).end(); return; }
    try {
      const path = await resolveRequestPath(project, req.url);
      served.add(path);
      res.writeHead(200, { 'Content-Type': mime[extname(path).toLowerCase()] ?? 'application/octet-stream', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin' });
      if (req.method === 'HEAD') res.end();
      else createReadStream(path).on('error', () => { failed(); res.destroy(); }).pipe(res);
    } catch (error) { failed(); res.writeHead(error.code === 'ENOENT' ? 404 : 403).end(); }
  });
  await new Promise((done, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', done); });
  return { origin: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((done) => { server.closeAllConnections(); server.close(done); }) };
}

async function findChrome() {
  const candidates = process.env.CHROME_PATH ? [process.env.CHROME_PATH] : [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    join(process.env.PROGRAMFILES ?? 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ];
  for (const candidate of candidates) if (await exists(candidate)) return candidate;
  throw new Error('Chrome was not found. Set CHROME_PATH to a Chrome or Chromium executable.');
}

function cdpConnect(url) {
  return new Promise((done, fail) => {
    const socket = new WebSocket(url);
    const pending = new Map();
    const listeners = new Set();
    let sequence = 0;
    let openTimer = setTimeout(() => { socket.close(); fail(new Error('Chrome connection timed out.')); }, 15000);
    const rejectPending = () => { for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error('Chrome connection closed.')); } pending.clear(); };
    socket.addEventListener('error', () => { clearTimeout(openTimer); fail(new Error('Chrome connection failed.')); rejectPending(); });
    socket.addEventListener('close', rejectPending);
    socket.addEventListener('message', (message) => {
      const data = JSON.parse(message.data);
      if (data.id !== undefined && pending.has(data.id)) {
        const request = pending.get(data.id); clearTimeout(request.timer); pending.delete(data.id);
        if (data.error) request.reject(new Error('Chrome rejected a rendering command.'));
        else request.resolve(data.result ?? {});
      } else if (data.method) for (const listener of listeners) listener(data.method, data.params ?? {});
    });
    socket.addEventListener('open', () => {
      clearTimeout(openTimer); openTimer = undefined;
      done({
        send(method, params = {}) {
          return new Promise((resolveCall, reject) => {
            const id = ++sequence;
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Chrome timed out during ${method}.`)); }, 30000);
            pending.set(id, { resolve: resolveCall, reject, timer });
            socket.send(JSON.stringify({ id, method, params }));
          });
        },
        on(listener) { listeners.add(listener); },
        close() { socket.close(); rejectPending(); },
      });
    });
  });
}

async function startBrowser(profile, children) {
  const chrome = spawn(await findChrome(), [
    '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--disable-background-networking',
    '--disable-component-update', '--disable-default-apps', '--disable-sync', '--metrics-recording-only',
    '--force-color-profile=srgb', '--autoplay-policy=no-user-gesture-required', 'about:blank',
  ], { stdio: 'ignore', detached: process.platform !== 'win32' });
  if (process.platform !== 'win32') groupedChildren.add(chrome);
  children.add(chrome);
  let launchError = false;
  chrome.on('error', () => { launchError = true; });
  let port;
  const limit = Date.now() + 20000;
  while (Date.now() < limit) {
    if (launchError || chrome.exitCode !== null || chrome.signalCode !== null) throw new Error('Chrome exited before its rendering session was ready.');
    try { port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); } catch {}
    if (Number.isInteger(port) && port > 0 && port < 65536) break;
    await delay(50);
  }
  if (!port) throw new Error('Chrome startup timed out.');
  const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT', signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error('Could not create the Chrome render target.');
  const target = await response.json();
  if (!/^ws:\/\/127\.0\.0\.1:\d+\//.test(target.webSocketDebuggerUrl)) throw new Error('Unexpected Chrome debugging address.');
  return cdpConnect(target.webSocketDebuggerUrl);
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error('Page JavaScript failed. Details are redacted; inspect the project locally.');
  return result.result?.value;
}

async function runTool(binary, args, children, label) {
  return new Promise((done, fail) => {
    const child = spawn(binary, args, { stdio: ['ignore', 'pipe', 'ignore'] });
    children.add(child);
    const chunks = []; let bytes = 0;
    child.stdout.on('data', (part) => { bytes += part.length; if (bytes <= 1048576) chunks.push(part); });
    child.on('error', () => { children.delete(child); fail(new Error(`${label} could not start. Check its installation or executable override.`)); });
    child.on('close', (code) => { children.delete(child); code === 0 ? done(Buffer.concat(chunks).toString('utf8')) : fail(new Error(`${label} failed (exit ${code ?? 'signal'}). Tool output is redacted to protect project data.`)); });
  });
}

async function publishFile(temporary, output, force) {
  if (force) await rename(temporary, output);
  else { await link(temporary, output); await rm(temporary); }
}

export async function render(options) {
  if (Number(process.versions.node.split('.')[0]) < 22 || typeof WebSocket === 'undefined') throw new Error('Node 22 or newer is required.');
  const project = await realpath(resolve(options.project));
  const inputs = new Set(['index.html', 'job.json', 'timing.json', 'narration-full.wav'].map((name) => join(project, name)));
  // Check boundaries before reading JSON or letting Chrome open the page.
  for (const input of [...inputs].filter((path) => options.frame === undefined || basename(path) !== 'narration-full.wav')) {
    if (!inside(project, await realpath(input))) throw new Error('Project inputs must remain inside the project directory.');
  }
  const jobBytes = await readFile(join(project, 'job.json'));
  const timingBytes = await readFile(join(project, 'timing.json'));
  const job = JSON.parse(jobBytes.toString('utf8'));
  const timing = JSON.parse(timingBytes.toString('utf8'));
  const manifestVerified = await verifyBuildManifest(project, new Map([['job.json', jobBytes], ['timing.json', timingBytes]]));
  if (manifestVerified) for (const name of ['data.json', 'build-manifest.json']) inputs.add(join(project, name));
  const config = validateConfig(job, timing);
  if (options.frame !== undefined && options.frame >= config.frameCount) throw new Error('--frame is outside the timeline.');
  const stillMode = options.frame !== undefined;
  const output = resolve(stillMode ? options.still : options.out ?? join(project, 'video.mp4'));
  const reportPath = join(project, 'render-report.json');
  await mkdir(dirname(output), { recursive: true });
  await assertOutputSafe(output, inputs, options.force);
  if (!stillMode) { await assertOutputSafe(reportPath, inputs, options.force); if (output === reportPath) throw new Error('Video and report outputs must differ.'); }
  const children = new Set();
  const temp = await mkdtemp(join(tmpdir(), 'anatomy-render-'));
  const pendingFiles = [];
  let server; let cdp; let interrupted = false;
  const stop = () => { interrupted = true; for (const child of children) killChild(child); cdp?.close(); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  const problems = [];
  const check = () => {
    if (interrupted) throw new Error('Rendering interrupted.');
    if (problems.length) throw new Error(`${problems[0]} Details are redacted; inspect the project locally.`);
  };
  try {
    const narrationDuration = stillMode ? null : validateNarrationProbe(JSON.parse(await runTool(process.env.FFPROBE_PATH ?? 'ffprobe', ['-v', 'error', '-show_entries', 'format=duration:stream=codec_type,duration', '-of', 'json', join(project, 'narration-full.wav')], children, 'Narration FFprobe')), config.requestedDuration);
    server = await serveProject(project, inputs, () => problems.push('Project resource failed to load.'));
    cdp = await startBrowser(join(temp, 'chrome-profile'), children);
    cdp.on((method, params) => {
      if (method === 'Runtime.exceptionThrown' || (method === 'Runtime.consoleAPICalled' && params.type === 'error')) problems.push('Page JavaScript error detected.');
      if (method === 'Fetch.requestPaused') {
        let allowed = false;
        try { const url = new URL(params.request.url); allowed = url.origin === server.origin || ['data:', 'blob:', 'about:'].includes(url.protocol); } catch {}
        if (!allowed) problems.push('External resource request rejected; vendor assets into the project.');
        void cdp.send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', allowed ? { requestId: params.requestId } : { requestId: params.requestId, errorReason: 'BlockedByClient' }).catch(() => problems.push('Resource interception failed.'));
      }
    });
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: config.width, height: config.height, deviceScaleFactor: 1, mobile: false });
    await cdp.send('Emulation.setDefaultBackgroundColorOverride', { color: { r: 255, g: 255, b: 255, a: 1 } });
    await cdp.send('Animation.enable');
    await cdp.send('Animation.setPlaybackRate', { playbackRate: 0 });
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `addEventListener('unhandledrejection',()=>console.error('unhandled promise rejection'));` });
    const navigation = await cdp.send('Page.navigate', { url: `${server.origin}/index.html` });
    if (navigation.errorText) throw new Error('Project page navigation failed.');
    const readyLimit = Date.now() + 30000;
    let ready = false;
    while (Date.now() < readyLimit) {
      check();
      ready = await evaluate(cdp, 'window.__VIDEO_READY__ === true && typeof window.renderFrame === "function"');
      if (ready) break;
      await delay(50);
    }
    if (!ready) throw new Error('Page did not expose __VIDEO_READY__ === true and renderFrame(t) within 30 seconds.');
    await evaluate(cdp, `(async()=>{await document.fonts.ready;await Promise.all(Array.from(document.images,img=>img.decode()));return true})()`);
    const frameNumbers = stillMode ? [options.frame] : Array.from({ length: config.frameCount }, (_, i) => i);
    const frames = join(temp, 'frames'); await mkdir(frames);
    let lastPresentation = null;
    const observedCues = new Set();
    const observedPlacements = new Set();
    let captionAlignment = null;
    for (const frame of frameNumbers) {
      check();
      lastPresentation = await evaluate(cdp, `(async()=>{
        await window.renderFrame(${frame / config.fps});await document.fonts.ready;
        await Promise.all(Array.from(document.images,img=>img.complete?Promise.resolve():img.decode()));
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
        const state=window.__PRESENTATION_STATE__;
        if(!state)return null;
        const node=document.getElementById('subtitle'),title=document.getElementById('title'),source=document.getElementById('source');
        const box=node.getBoundingClientRect(),tb=title.getBoundingClientRect(),sb=source.getBoundingClientRect();
        const overlaps=(a,b)=>a.left<b.right-1&&a.right>b.left+1&&a.top<b.bottom-1&&a.bottom>b.top+1;
        const visible=state.caption.opacity>.05&&node.textContent.trim().length>0;
        return {scene_id:state.scene_id,cue_id:state.caption.cue?.id||null,alignment:state.alignment,
          placement:state.caption.placement,visible,
          clipped:visible&&(box.left<-.5||box.top<-.5||box.right>innerWidth+.5||box.bottom>innerHeight+.5),
          overflow:visible&&(node.scrollHeight>node.clientHeight+2||node.scrollWidth>node.clientWidth+2),
          overlaps_title:visible&&overlaps(box,tb),overlaps_source:visible&&overlaps(box,sb),
          title_clipped:title.scrollHeight>title.clientHeight+2,
          caption_box:{x:box.x,y:box.y,width:box.width,height:box.height}};
      })()`);
      if (lastPresentation) {
        if (lastPresentation.clipped || lastPresentation.overflow || lastPresentation.overlaps_title || lastPresentation.overlaps_source || lastPresentation.title_clipped) throw new Error('Presentation text failed safe-area or overflow verification. Review caption position and title length.');
        if (lastPresentation.visible && lastPresentation.cue_id) observedCues.add(lastPresentation.cue_id);
        observedPlacements.add(lastPresentation.placement);
        captionAlignment = lastPresentation.alignment;
      }
      check();
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false, clip: { x: 0, y: 0, width: config.width, height: config.height, scale: 1 } });
      if (!shot.data) throw new Error('Chrome returned no screenshot.');
      await writeFile(join(frames, `${String(frame).padStart(8, '0')}.png`), Buffer.from(shot.data, 'base64'));
      check();
    }
    await assertOutputSafe(output, inputs, options.force);
    if (stillMode) {
      const temporary = join(dirname(output), `.anatomy-still-${process.pid}-${Date.now()}.png`); pendingFiles.push(temporary);
      await writeFile(temporary, await readFile(join(frames, `${String(options.frame).padStart(8, '0')}.png`)), { flag: 'wx' });
      await publishFile(temporary, output, options.force);
      return { mode: 'still', output, frame: options.frame, time: options.frame / config.fps, width: config.width, height: config.height, presentation: lastPresentation, evidence: 'Chrome CDP screenshot after renderFrame(frame / fps)' };
    }
    await assertOutputSafe(reportPath, inputs, options.force);
    const temporary = join(dirname(output), `.anatomy-video-${process.pid}-${Date.now()}.mp4`); pendingFiles.push(temporary);
    await runTool(process.env.FFMPEG_PATH ?? 'ffmpeg', [
      '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-framerate', String(config.fps), '-start_number', '0', '-i', join(frames, '%08d.png'),
      '-i', join(project, 'narration-full.wav'), '-filter_complex', `[1:a]apad=whole_dur=${config.duration}[audio]`,
      '-map', '0:v:0', '-map', '[audio]', '-frames:v', String(config.frameCount), '-t', String(config.duration),
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(config.fps),
      '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', temporary,
    ], children, 'FFmpeg');
    const probe = JSON.parse(await runTool(process.env.FFPROBE_PATH ?? 'ffprobe', ['-v', 'error', '-count_frames', '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,duration,pix_fmt', '-of', 'json', temporary], children, 'FFprobe'));
    const video = probe.streams?.find((stream) => stream.codec_type === 'video');
    const audio = probe.streams?.find((stream) => stream.codec_type === 'audio');
    const [numerator, denominator = 1] = String(video?.avg_frame_rate ?? '').split('/').map(Number);
    const actualFps = numerator / denominator;
    if (!video || !audio || video.codec_name !== 'h264' || audio.codec_name !== 'aac' || video.pix_fmt !== 'yuv420p' || video.width !== config.width || video.height !== config.height || Number(video.nb_read_frames) !== config.frameCount || Math.abs(actualFps - config.fps) > 0.001 || Math.abs(Number(video.duration) - config.duration) > 0.002) throw new Error('Encoded output failed resolution, duration, codec, fps or frame-count verification.');
    const report = {
      schemaVersion: 1, mode: 'video', output: relative(project, output).split(sep).join('/'),
      requestedDuration: config.requestedDuration, narrationDuration, duration: Number(probe.format.duration), videoDuration: Number(video.duration),
      width: video.width, height: video.height, fps: actualFps, frameCount: Number(video.nb_read_frames),
      videoCodec: video.codec_name, audioCodec: audio.codec_name, pixelFormat: video.pix_fmt,
      presentation: captionAlignment ? {alignment:captionAlignment,visibleCueCount:observedCues.size,placements:[...observedPlacements],safeAreasPassed:true} : null,
      evidence: { capture: 'one CDP PNG per renderFrame(frame / fps)', timeline: 'ceil(timing.duration * fps) / fps', audio: 'narration duration verified within 0.01s of timing.duration; apad extends only to the rounded frame timeline', buildManifestVerified: manifestVerified, verification: 'ffprobe -count_frames', pageJavaScriptErrors: 0, externalRequests: 0, wallClockRecording: false },
    };
    const reportTemp = join(project, `.anatomy-report-${process.pid}-${Date.now()}.json`); pendingFiles.push(reportTemp);
    await writeFile(reportTemp, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
    check();
    await assertOutputSafe(output, inputs, options.force); await assertOutputSafe(reportPath, inputs, options.force);
    await publishFile(temporary, output, options.force);
    await publishFile(reportTemp, reportPath, options.force);
    return { ...report, output, report: reportPath };
  } finally {
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
    cdp?.close();
    for (const child of children) killChild(child);
    if (server) await server.close();
    for (const path of pendingFiles) await rm(path, { force: true }).catch(() => {});
    await rm(temp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) console.log(USAGE);
    else console.log(JSON.stringify(await render(options), null, 2));
  } catch (error) {
    // Never echo browser exceptions, subprocess stderr, config contents, or credentials.
    const safe = error instanceof SyntaxError ? 'Invalid JSON in project inputs or media probe.' : error.code ? `Filesystem operation failed (${error.code}); check project inputs and output permissions.` : error.message;
    console.error(`Render failed: ${safe}`);
    process.exitCode = 1;
  }
}
