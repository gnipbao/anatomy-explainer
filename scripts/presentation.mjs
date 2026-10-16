import {buildCinemaTrack} from './subtitles.mjs';

/**
 * Deterministic presentation state, with no DOM, clock or random dependency.
 * Coordinates are percentages of the film, in the safe areas from video.css.
 */
const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;
const smooth = value => { const n = clamp(value); return n * n * (3 - 2 * n); };
const isCJK = value => /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(value);

// Latin words (including contractions, numbers and hyphenated terms) stay whole.
const units = text => text.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]|(?:(?![\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}])[\p{L}\p{N}\p{M}])+(?:['’.-](?:(?![\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}])[\p{L}\p{N}\p{M}])+)*|\s+|[^\s]/gu) || [];
const lengthOf = text => Array.from(text).reduce((n, c) => n + (isCJK(c) ? 1 : /\s/u.test(c) ? .25 : .52), 0);

function splitLong(text, limit) {
  const result = [];
  let part = '';
  for (const token of units(text)) {
    if (part.trim() && lengthOf(part + token) > limit) {
      result.push(part.trim());
      part = token.trimStart();
    } else part += token;
  }
  if (part.trim()) result.push(part.trim());
  return result;
}

/** Short phrases for the explicitly estimated fallback; never claims word sync. */
export function splitCaptionText(value) {
  const text = String(value || '').replace(/\s+/gu, ' ').trim();
  if (!text) return [];
  // A decimal point or a dot inside an English word is not a phrase boundary.
  const clauses = text.split(/(?<=[。！？!?；;，,：:])|(?<=\.)(?=\s|$)/u)
    .flatMap(part => splitLong(part.trim(), 26)).filter(Boolean);
  const phrases = [];
  let part = '';
  for (const clause of clauses) {
    const separator = part && !isCJK(part.at(-1)) && !isCJK(clause[0]) ? ' ' : '';
    const joined = part + separator + clause;
    if (!part || lengthOf(joined) <= 26 || (lengthOf(part) < 12 && lengthOf(joined) <= 32)) part = joined;
    else { phrases.push(part); part = clause; }
  }
  if (part) {
    const previous = phrases.at(-1);
    const separator = previous && !isCJK(previous.at(-1)) && !isCJK(part[0]) ? ' ' : '';
    if (previous && lengthOf(part) < 8 && lengthOf(previous + separator + part) <= 32) phrases[phrases.length - 1] += separator + part;
    else phrases.push(part);
  }
  return phrases;
}

function estimatedCues(scene, track) {
  const phrases = splitCaptionText(scene.narration);
  // New TTS rows may exclude the deliberate end hold. Old measured scene rows
  // include it, so subtract only the declared hold, never invent speech timing.
  const duration = track.end - track.start;
  const speech = finite(track.speech_duration, finite(track.narration_duration,
    Math.max(.001, duration - Math.max(0, finite(scene.hold_seconds, 0)))));
  const end = Math.min(track.end, track.start + clamp(speech, .001, duration));
  const weights = phrases.map(phrase => Math.max(1, lengthOf(phrase)));
  const total = weights.reduce((sum, n) => sum + n, 0);
  let elapsed = track.start;
  return phrases.map((text, index) => {
    const next = index === phrases.length - 1 ? end : elapsed + (end - track.start) * weights[index] / total;
    const cue = { id: `${scene.id}-estimated-${index + 1}`, scene_id: scene.id, start: elapsed, end: next, text };
    elapsed = next;
    return cue;
  });
}

const landscapeBoxes = Object.freeze({
  left: { x: 4.8, y: 58, width: 26.5, height: 24, align: 'left' },
  bottom: { x: 35.5, y: 85.7, width: 59.5, height: 9.1, align: 'center' },
  'bottom-left': { x: 35.5, y: 85.7, width: 42, height: 9.1, align: 'left' },
  right: { x: 64, y: 85.7, width: 31, height: 9.1, align: 'right' },
  top: { x: 35.5, y: 6.2, width: 59.5, height: 9.5, align: 'left' },
});
const portraitBoxes = Object.freeze({
  left: { x: 7, y: 78.5, width: 75, height: 13.2, align: 'left' },
  bottom: { x: 7, y: 78.5, width: 86, height: 13.2, align: 'center' },
  'bottom-left': { x: 7, y: 78.5, width: 80, height: 13.2, align: 'left' },
  right: { x: 18, y: 78.5, width: 75, height: 13.2, align: 'right' },
  top: { x: 7, y: 25.5, width: 86, height: 8, align: 'left' },
});
const autoPlacements = ['left', 'bottom', 'bottom-left', 'right'];

function wrapCaption(text, capacity) {
  const normalized = text.replace(/\s+/gu, ' ').trim();
  if (lengthOf(normalized) <= capacity) return [normalized];
  const tokens = units(normalized);
  const target = lengthOf(normalized) / 2;
  let width = 0, best = -1, distance = Infinity;
  for (let index = 1; index < tokens.length; index++) {
    width += lengthOf(tokens[index - 1]);
    // Do not put closing punctuation at the beginning of a new line.
    if (/^[，。！？、；：,.!?;:）》】]/u.test(tokens[index])) continue;
    const delta = Math.abs(width - target);
    if (delta < distance) { best = index; distance = delta; }
  }
  if (best < 0) return [normalized]; // One unbreakable word is kept intact.
  return [tokens.slice(0, best).join('').trim(), tokens.slice(best).join('').trim()].filter(Boolean);
}

function kineticCaptionLayout(job, scene, sceneIndex, cueIndex, mode, text) {
  const portrait = finite(job.video?.height, 1080) > finite(job.video?.width, 1920);
  const boxes = portrait ? portraitBoxes : landscapeBoxes;
  // An explicit global position is a deliberate override of scene positions.
  const global = job.presentation?.captions?.placement;
  const requested = global && global !== 'auto' ? global : scene.presentation?.caption_placement || global || 'auto';
  const placement = boxes[requested] ? requested : mode === 'scene' ? 'bottom' : autoPlacements[(sceneIndex + Math.max(0, cueIndex)) % autoPlacements.length];
  const box = { ...boxes[placement] };
  if (mode === 'scene' && !portrait && requested === 'auto') Object.assign(box, { x: 4.8, width: 90.2 });
  const width = finite(job.video?.width, 1920), height = finite(job.video?.height, 1080);
  const unit = portrait ? width / 1080 : Math.min(width / 1920, height / 1080);
  const fontSize = portrait ? (mode === 'scene' ? 34 : 42) : mode === 'scene' ? 25 : 34;
  const inset = placement === 'left' && !portrait ? 20 * unit : 0;
  const capacity = (box.width / 100 * width - inset) / Math.max(1, fontSize * unit);
  const fontScale = clamp(capacity * 2 / Math.max(1, lengthOf(text)), .55, 1);
  const lines = wrapCaption(text, capacity / fontScale);
  return { placement, ...box, font_size: fontSize, font_scale: fontScale, lines, display_text: lines.join('\n') };
}

function focusFor(value) {
  return Array.isArray(value) ? [clamp(finite(value[0], .5)), clamp(finite(value[1], .5))]
    : [clamp(finite(value?.x, .5)), clamp(finite(value?.y, .5))];
}

function cinemaCaptionLayout(job, cue) {
  const portrait = finite(job.video?.height, 1080) > finite(job.video?.width, 1920);
  // Film subtitles use one stable full-frame center, independent of stage layout,
  // theme, scene and cue length. Both one and two lines share the bottom baseline.
  const box = portrait
    ? {x: 8, y: 81, width: 84, height: 11, align: 'center'}
    : {x: 10, y: 84, width: 80, height: 11, align: 'center'};
  return {
    placement: 'bottom', ...box, font_size: 42, font_scale: 1,
    lines: cue?.lines || [], display_text: cue?.display_text || '',
  };
}

/**
 * Current scene/progress, one caption cue and deterministic camera/pane values.
 * Cinema uses a derived frame-timed display track; cue/source_text retain the
 * original narration cue. No word alignment is inferred from phrase durations.
 */
export function computePresentation(job, timing, time) {
  if (!Number.isFinite(time)) throw new TypeError('presentation time must be finite');
  if (!job?.scenes?.length || !timing?.scenes?.length || !Number.isFinite(timing.duration) || timing.duration <= 0) throw new TypeError('presentation requires measured scene timing');
  const t = clamp(time, 0, Math.max(0, timing.duration - .000001));
  let index = timing.scenes.findIndex(track => t >= track.start && t < track.end);
  if (index < 0) index = t < timing.scenes[0].start ? 0 : timing.scenes.length - 1;
  const track = timing.scenes[index];
  const scene = job.scenes.find(item => item.id === track.id) || job.scenes[index];
  if (!scene) throw new TypeError('presentation scene does not match timing');
  const duration = Math.max(.000001, track.end - track.start);
  const progress = clamp((t - track.start) / duration);
  const mode = job.presentation?.captions?.mode === 'scene' ? 'scene' : 'phrase';
  // Explicit whole-scene mode remains a legacy escape hatch. It uses the old
  // presentation instead of squeezing an entire narration into cinema lines.
  const style = mode === 'scene' || job.presentation?.captions?.style === 'kinetic' ? 'kinetic' : 'cinema';
  const suppliedCues = mode !== 'scene' && ['measured-phrases', 'manual'].includes(timing.captions?.alignment) && Array.isArray(timing.captions.cues);
  const alignment = mode === 'scene' ? 'scene' : suppliedCues ? timing.captions.alignment : 'estimated-phrases';
  const sourceCues = mode === 'scene' ? [{ id: `${scene.id}-scene`, scene_id: scene.id, start: track.start, end: track.end, text: scene.narration }]
    : suppliedCues ? timing.captions.cues.filter(cue => Number.isFinite(cue.start) && Number.isFinite(cue.end) && cue.end > cue.start && typeof cue.text === 'string')
    : timing.scenes.flatMap((row, i) => estimatedCues(job.scenes.find(item => item.id === row.id) || job.scenes[i], row));
  // Derive the complete cinema track, so random seeks and nearby scene changes
  // cannot change frame rounding or the two-frame gap decisions.
  const cinemaTrack = style === 'cinema' ? buildCinemaTrack(job, {...timing, captions: {...timing.captions, alignment}}, sourceCues) : null;
  // Frame rounding may place a display boundary just before or after its raw
  // scene boundary. Query cinema globally so SRT and burn-in share every frame;
  // the mechanism/camera scene continues using the original scene timing.
  const cues = cinemaTrack?.cues || sourceCues.filter(cue => cue.scene_id === scene.id);
  // Half-open frame-aligned intervals prevent a stale sentence at the next cue.
  const cueIndex = cues.findIndex(cue => t >= cue.start && t < cue.end);
  const displayCue = cueIndex >= 0 ? { ...cues[cueIndex] } : null;
  const cue = displayCue ? { ...(displayCue.source || displayCue) } : null;
  const reducedMotion = job.presentation?.reduced_motion === true;
  const cueDuration = displayCue ? displayCue.end - displayCue.start : 0;
  const fade = displayCue && style === 'kinetic' ? Math.min(.2, cueDuration * .24) : 0;
  const opacity = !displayCue?.text ? 0 : style === 'cinema' || reducedMotion || mode === 'scene' ? 1
    : Math.min(smooth((t - displayCue.start) / fade), smooth((displayCue.end - t) / fade));
  const text = displayCue?.text || '';
  const layout = style === 'cinema' ? cinemaCaptionLayout(job, displayCue)
    : kineticCaptionLayout(job, scene, index, cueIndex, mode, text);
  const entrance = smooth((t - track.start) / Math.min(.42, duration * .18));
  const focus = focusFor(scene.presentation?.focus || job.presentation?.focus);
  const zoom = clamp(finite(scene.presentation?.zoom, finite(job.presentation?.zoom, 1.035)), 1, 1.12);
  const eased = smooth(progress);
  const camera = reducedMotion ? { x: 0, y: 0, scale: 1, origin_x: 50, origin_y: 50 } : {
    x: (0.5 - focus[0]) * 3 * eased + (index % 2 ? -.32 : .32) * Math.sin(progress * Math.PI),
    y: (0.5 - focus[1]) * 3 * eased - .3 * eased,
    scale: 1 + (zoom - 1) * eased,
    origin_x: focus[0] * 100,
    origin_y: focus[1] * 100,
  };
  return {
    time: t, index, scene, scene_id: scene.id, progress, alignment,
    estimated: alignment === 'estimated-phrases', reduced_motion: reducedMotion,
    caption: {
      text, cue, source_text: cue?.text || '', display_cue: displayCue,
      source_start: displayCue?.source_start ?? cue?.start ?? null,
      source_end: displayCue?.source_end ?? cue?.end ?? null,
      start: displayCue?.start ?? null, end: displayCue?.end ?? null,
      start_frame: displayCue?.start_frame ?? null, end_frame: displayCue?.end_frame ?? null,
      cue_index: cueIndex, count: cues.length, mode, style, alignment,
      estimated: alignment === 'estimated-phrases', opacity, ...layout,
      offset_y: style === 'cinema' || reducedMotion ? 0 : (1 - opacity) * .65,
      fade_seconds: fade,
      qc: cinemaTrack ? {issues: cinemaTrack.issues, profile: cinemaTrack.profile,
        overflow: displayCue?.overflow || false, reading_speed: displayCue?.reading_speed ?? null,
        line_limit: displayCue?.line_limit ?? null, duration: displayCue?.duration ?? null} : null,
    },
    camera,
    pane: { opacity: reducedMotion ? 1 : .72 + .28 * entrance, offset_y: reducedMotion ? 0 : 8 * (1 - entrance) },
    reveal: { scene: progress, film: clamp(t / timing.duration), completed: index },
  };
}
