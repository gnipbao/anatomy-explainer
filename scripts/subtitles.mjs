/**
 * Shared, deterministic cinema subtitle layout and QC.
 * Display timing is rounded to frames; source timing remains an audit trail.
 * This is a documented production profile, not a claim of platform compliance.
 */
const ZH = /^zh(?:-|$)/i;
const OPEN = /[（(\[【《〈「『“‘{]$/u;
const CLOSE = /^[，。！？、；：,.!?;:）)\]】》〉」』”’}%]/u;
const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
const graphemes = value => typeof Intl?.Segmenter === 'function'
  ? Array.from(new Intl.Segmenter('und', { granularity: 'grapheme' }).segment(value), part => part.segment)
  : Array.from(value);
const isChinese = language => ZH.test(String(language || 'zh-CN'));
const widthOf = (text, chinese) => graphemes(text).reduce((sum, char) => sum + (chinese && /^[\x00-\x7F]+$/u.test(char) ? char.length / 2 : 1), 0);

function normalizeText(value, chinese) {
  let text = String(value ?? '').replace(/\s+/gu, ' ').trim();
  if (chinese) {
    text = text.replace(/[，。]/gu, ' ')
      // Keep decimal points, domains and numerical group separators intact.
      .replace(/[,.]/gu, (char, index, whole) => {
        const previous = whole[index - 1] || '', next = whole[index + 1] || '';
        if (char === ',' && /\d/u.test(previous) && /\d/u.test(next)) return char;
        if (char === '.' && /[A-Za-z\d]/u.test(previous) && /[A-Za-z\d]/u.test(next)) return char;
        return ' ';
      }).replace(/\s+/gu, ' ').trim();
  }
  return text;
}

function layoutAtoms(text, keepTogether, chinese) {
  const protectedTerms = Array.from(new Set((Array.isArray(keepTogether) ? keepTogether : [])
    .filter(term => typeof term === 'string').map(term => normalizeText(term, chinese)).filter(Boolean)))
    .sort((a, b) => b.length - a.length || a.localeCompare(b));
  const chars = new Map();
  let charOffset = 0;
  for (const char of graphemes(text)) { chars.set(charOffset, char); charOffset += char.length; }
  const atoms = [];
  let offset = 0;
  while (offset < text.length) {
    const term = protectedTerms.find(item => text.startsWith(item, offset));
    if (term) { atoms.push(term); offset += term.length; continue; }
    const rest = text.slice(offset);
    // Latin words, numbers and technical identifiers stay intact when possible.
    const word = rest.match(/^[\p{L}\p{N}]+(?:['’._/\-][\p{L}\p{N}]+)*[%°]?/u)?.[0];
    if (word && !CJK.test(word)) {
      // A protected term that begins inside a Latin token must still stay whole.
      const upcoming = protectedTerms.map(item => text.indexOf(item, offset + 1))
        .filter(index => index >= offset && index < offset + word.length).sort((a, b) => a - b)[0];
      const atom = upcoming === undefined ? word : text.slice(offset, upcoming);
      atoms.push(atom); offset += atom.length; continue;
    }
    const char = chars.get(offset) || graphemes(rest)[0];
    atoms.push(char); offset += char.length;
  }
  return atoms;
}

/** Two lines at most; no font shrinking and no invented speech boundaries. */
export function layoutCinemaText(value, language = 'zh-CN', keepTogether = []) {
  const chinese = isChinese(language), line_limit = chinese ? 16 : 42;
  const text = normalizeText(value, chinese);
  if (!text) return { text, lines: [], display_text: '', line_limit, overflow: false };
  if (widthOf(text, chinese) <= line_limit) return { text, lines: [text], display_text: text, line_limit, overflow: false };
  const atoms = layoutAtoms(text, keepTogether, chinese), candidates = [];
  for (let index = 1; index < atoms.length; index++) {
    const first = atoms.slice(0, index).join('').trim(), second = atoms.slice(index).join('').trim();
    if (!first || !second || OPEN.test(first) || CLOSE.test(second)) continue;
    const a = widthOf(first, chinese), b = widthOf(second, chinese);
    const excess = Math.max(0, a - line_limit) + Math.max(0, b - line_limit);
    const boundary = atoms[index - 1] + atoms[index];
    const semantic = /\s/u.test(boundary) || /[，。！？、；：,.!?;:）)\]】》〉」』”’]$/u.test(first);
    // Prefer semantic boundaries and a slightly longer lower line, without
    // sacrificing either line's hard limit to obtain that shape.
    const score = excess * 1000 + (semantic ? 0 : 6) + Math.abs(a - b) * .55 + Math.max(0, a - b) * .8;
    candidates.push({ lines: [first, second], score, overflow: excess > 0 });
  }
  const best = candidates.sort((a, b) => a.score - b.score)[0];
  const lines = best?.lines || [text];
  return { text, lines, display_text: lines.join('\n'), line_limit, overflow: best ? best.overflow : true };
}

const clone = value => Array.isArray(value) ? value.map(clone) : value && typeof value === 'object'
  ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)])) : value;
const issue = (code, cue, message, detail = {}, severity = 'warning') => ({ code, severity, cue_id: cue.id, scene_id: cue.scene_id, message, ...detail });

/** Build a whole-film track once, then query it in any frame order. */
export function buildCinemaTrack(job, timing, sourceCues = timing?.captions?.cues || []) {
  if (!Array.isArray(sourceCues)) throw new TypeError('subtitle cues must be an array');
  const fps = job?.video?.fps ?? 24;
  if (!Number.isFinite(fps) || fps <= 0) throw new TypeError('subtitle fps must be a positive finite number');
  const language = job?.language || 'zh-CN', chinese = isChinese(language);
  const profile = { language, fps, line_limit: chinese ? 16 : 42, max_lines: 2,
    min_duration: 5 / 6, max_duration: 7, min_gap_frames: 2,
    reading_speed_limit: chinese ? 9 : 20, width_unit: chinese ? 'fullwidth-equivalent' : 'characters' };
  const keepTogether = job?.presentation?.captions?.keep_together || [];
  const cues = sourceCues.map((source, index) => {
    if (!source || typeof source.text !== 'string' || !Number.isFinite(source.start) || !Number.isFinite(source.end) || source.start < 0 || source.end <= source.start) {
      throw new TypeError(`subtitle cue ${index + 1} requires text and a valid positive time interval`);
    }
    const layout = layoutCinemaText(source.text, language, keepTogether);
    return { source: clone(source), id: source.id || `cue-${index + 1}`, scene_id: source.scene_id,
      source_text: source.text, source_start: source.start, source_end: source.end,
      start_frame: Math.round(source.start * fps), end_frame: Math.round(source.end * fps), ...layout };
  }).sort((a, b) => a.start_frame - b.start_frame || a.source_start - b.source_start || String(a.id).localeCompare(String(b.id)));
  const issues = [];
  // Only trim the previous cue's tail. Never shift or extend a following cue,
  // and never make a cue zero-length merely to fabricate the required gap.
  for (let index = 1; index < cues.length; index++) {
    const previous = cues[index - 1], current = cues[index];
    const wantedEnd = current.start_frame - profile.min_gap_frames;
    if (previous.end_frame > wantedEnd) {
      if (wantedEnd > previous.start_frame) previous.end_frame = wantedEnd;
      else issues.push(issue('minimum-gap', previous, 'Cannot create a two-frame gap without eliminating the previous cue; review the source timing.', { next_cue_id: current.id, actual_gap_frames: current.start_frame - previous.end_frame }));
    }
  }
  for (const cue of cues) {
    cue.start = cue.start_frame / fps;
    cue.end = cue.end_frame / fps;
    cue.duration = (cue.end_frame - cue.start_frame) / fps;
    // Manual layout line breaks do not add reading units. Source-normalized
    // spaces and punctuation do, matching the documented subtitle profile.
    cue.reading_speed = cue.duration > 0 ? widthOf(cue.text, chinese) / cue.duration : null;
    if (cue.end_frame <= cue.start_frame) issues.push(issue('invalid-frame-duration', cue, 'Frame rounding leaves no visible duration; correct the source timing before exporting SRT.', { start_frame: cue.start_frame, end_frame: cue.end_frame }, 'error'));
    if (!cue.text) issues.push(issue('empty-text', cue, 'The cue has no visible text.'));
    if (cue.overflow) issues.push(issue('line-overflow', cue, 'Text cannot fit within two lines at the profile line limit; revise the text or source cue boundaries.', { line_limit: cue.line_limit }, 'error'));
    if (cue.duration < profile.min_duration) issues.push(issue('minimum-duration', cue, 'Cue is shorter than 5/6 second; review the original timing rather than inventing word boundaries.', { duration: cue.duration, minimum: profile.min_duration }));
    if (cue.duration > profile.max_duration) issues.push(issue('maximum-duration', cue, 'Cue is longer than seven seconds; review the source narration and cue boundaries.', { duration: cue.duration, maximum: profile.max_duration }));
    if (cue.reading_speed !== null && cue.reading_speed > profile.reading_speed_limit) issues.push(issue('reading-speed', cue, 'Cue exceeds the profile reading speed; shorten the wording or revise the source narration timing.', { reading_speed: cue.reading_speed, maximum: profile.reading_speed_limit }));
  }
  return { style: 'cinema', alignment: timing?.captions?.alignment || 'estimated-phrases', cues, issues, profile };
}

function srtTimestamp(seconds, frame, fps) {
  // SRT has millisecond precision. Floor both endpoints directly from integer
  // frames so parsing the SRT selects exactly the same video-frame set; rounding
  // upward would delay a start or retain an ended cue for one extra frame.
  const total = Math.max(0, Number.isInteger(frame) && Number.isFinite(fps) && fps > 0
    ? Math.floor(frame * 1000 / fps) : Math.floor(seconds * 1000));
  const hours = Math.floor(total / 3600000), minutes = Math.floor(total / 60000) % 60;
  const secs = Math.floor(total / 1000) % 60, millis = total % 1000;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')},${String(millis).padStart(3, '0')}`;
}

export function subtitlesToSrt(track) {
  if (!Array.isArray(track?.cues)) throw new TypeError('SRT requires a subtitle track');
  if (track.cues.some(cue => !Number.isFinite(cue.start) || !Number.isFinite(cue.end) || cue.start < 0 || cue.end <= cue.start)) throw new TypeError('SRT requires positive frame-aligned cue durations; correct the source timing');
  return track.cues.map((cue, index) => `${index + 1}\n${srtTimestamp(cue.start,cue.start_frame,track.profile?.fps)} --> ${srtTimestamp(cue.end,cue.end_frame,track.profile?.fps)}\n${cue.display_text}\n`).join('\n');
}

async function runCli(args) {
  const fs = await import('node:fs/promises'), path = await import('node:path');
  const options = {};
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === '--help' || flag === '-h') {
      process.stdout.write('Usage: node scripts/subtitles.mjs --project DIR [--out-srt PATH] [--report PATH] [--force]\nWrites cinema SRT and QC inside the project; --force replaces only the designated subtitle outputs.\n'); return;
    }
    if (flag === '--force') { options.force = true; continue; }
    if (!['--project', '--out-srt', '--out-SRT', '--report'].includes(flag) || !args[index + 1] || args[index + 1].startsWith('--')) throw new TypeError(`Unknown or incomplete subtitle option: ${flag}`);
    options[flag.toLowerCase()] = args[++index];
  }
  if (!options['--project']) throw new TypeError('--project DIR is required');
  const project = path.resolve(options['--project']);
  const inputPaths = ['job.json', 'timing.json'].map(file => path.join(project, file));
  const srtPath = path.resolve(options['--out-srt'] || path.join(project, 'cinematic.srt'));
  const reportPath = path.resolve(options['--report'] || path.join(project, 'subtitle-qc.json'));
  const outputs = [srtPath, reportPath];
  const protectedNames = new Set(['job.json', 'timing.json', 'data.json', 'build-manifest.json',
    'manifest.json', 'validation.json', 'package.json', 'package-lock.json', 'upstream.lock.json',
    'brief.json', 'research.json', 'sources.json', 'motion.json', 'storyboard.json', 'render-report.json',
    'narration-full.wav', 'model.mjs', 'mechanism.mjs', 'index.html', 'runtime.mjs',
    'presentation.mjs', 'subtitles.mjs', 'video.css', 'video.mp4']);
  if (outputs[0] === outputs[1] || outputs.some(file => protectedNames.has(path.basename(file).toLowerCase()))) throw new TypeError('Subtitle outputs must be distinct from protected project inputs and each other');
  if (path.extname(srtPath).toLowerCase() !== '.srt' || path.extname(reportPath).toLowerCase() !== '.json') throw new TypeError('Subtitle outputs must use .srt and .json extensions');
  for (const output of outputs) {
    const relative = path.relative(project, output);
    if (!relative || relative.startsWith(`..${path.sep}`) || relative === '..' || path.isAbsolute(relative)) throw new TypeError('Subtitle outputs must stay inside the project directory');
    let current = project;
    // A symbolic link in an output path could redirect --force to an input or
    // another project. Reject it even when its current destination seems safe.
    for (const component of relative.split(path.sep)) {
      current = path.join(current, component);
      let stat;
      try { stat = await fs.lstat(current); } catch (error) { if (error.code === 'ENOENT') continue; throw error; }
      if (stat.isSymbolicLink()) throw new TypeError('Subtitle outputs must be distinct ordinary files; symbolic links are not allowed');
      if (current === output) {
        if (!stat.isFile()) throw new TypeError('Subtitle outputs must be ordinary files');
        if (!options.force) throw new TypeError('Subtitle output already exists; review it and use --force to replace this designated output');
      }
    }
  }
  const [job, timing] = await Promise.all(inputPaths.map(async file => JSON.parse(await fs.readFile(file, 'utf8'))));
  if (!Array.isArray(timing.captions?.cues)) throw new TypeError('timing.json needs explicit caption cues; this CLI does not invent phrase timestamps');
  const track = buildCinemaTrack(job, timing);
  const payloads = [subtitlesToSrt(track), `${JSON.stringify(track, null, 2)}\n`];
  const pending = [];
  try {
    for (let index = 0; index < outputs.length; index++) {
      const output = [srtPath, reportPath][index];
      await fs.mkdir(path.dirname(output), { recursive: true });
      const temp = `${output}.tmp-${process.pid}-${index}`;
      await fs.writeFile(temp, payloads[index], { encoding: 'utf8', flag: 'wx' });
      pending.push({ temp, output });
    }
    for (const { temp, output } of pending) {
      if (options.force) await fs.rename(temp, output);
      else { await fs.link(temp, output); await fs.unlink(temp); }
    }
  } finally {
    await Promise.all(pending.map(({ temp }) => fs.rm(temp, { force: true })));
  }
  process.stdout.write(`${JSON.stringify({ style: track.style, alignment: track.alignment, cues: track.cues.length, errors: track.issues.filter(item => item.severity === 'error').length, warnings: track.issues.filter(item => item.severity === 'warning').length, srt: srtPath, report: reportPath })}\n`);
}

if (typeof process !== 'undefined' && process.versions?.node && process.argv[1]) {
  const { pathToFileURL } = await import('node:url');
  if (import.meta.url === pathToFileURL(process.argv[1]).href) {
    try { await runCli(process.argv.slice(2)); }
    catch (error) { process.stderr.write(`Subtitles: ${error.message}\n`); process.exitCode = 1; }
  }
}
