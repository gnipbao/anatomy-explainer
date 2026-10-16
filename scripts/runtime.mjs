import * as kit from './assets/kit/iso-kit.mjs';
import {mount} from './mechanism.mjs';
import {computePresentation} from './presentation.mjs';

const data = await (await fetch('./data.json')).json();
const {job, timing, theme} = data;
const $ = id => document.getElementById(id);
document.documentElement.lang = job.language;
document.body.dataset.theme = theme.base;
for (const [key, value] of Object.entries(theme.tokens)) document.body.style.setProperty(key, value);
const film = $('film');
const portrait = job.video.height > job.video.width;
const unit = portrait ? job.video.width / 1080 : Math.min(job.video.width / 1920, job.video.height / 1080);
film.style.width = job.video.width + 'px';
film.style.height = job.video.height + 'px';
film.style.setProperty('--u', unit);
film.dataset.portrait = String(portrait);

// Adapt the original portable project DOM without changing mechanism IDs or SVG.
const plate = $('iso-plate') || film.querySelector('.iso-plate');
plate.id = 'iso-plate';
const rail = document.createElement('aside');
rail.id = 'knowledge-rail';
film.prepend(rail);
rail.append($('eyebrow'));
const chapters = document.createElement('div');
chapters.id = 'scene-progress';
chapters.setAttribute('aria-hidden', 'true');
const bars = job.scenes.map(() => {
  const bar = document.createElement('span');
  const fill = document.createElement('i');
  bar.append(fill); chapters.append(bar); return fill;
});
rail.append(chapters);
const chapter = document.createElement('div');
chapter.id = 'rail-index';
rail.append(chapter, $('title'), $('caption'));
const camera = document.createElement('div');
camera.id = 'mechanism-camera';
plate.append(camera);
camera.append($('mechanism'));
const layer = document.createElement('div');
layer.id = 'caption-layer';
film.append(layer);
layer.append($('subtitle'));
$('subtitle').setAttribute('aria-live', 'off');
$('topic').textContent = job.topic;
$('eyebrow').textContent = job.language.startsWith('zh') ? '原理实验室 / ANATOMY' : 'MECHANISM LAB / ANATOMY';

const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const renderJob = prefersReducedMotion && job.presentation?.reduced_motion !== false
  ? {...job, presentation: {...job.presentation, reduced_motion: true}} : job;
const mechanism = mount($('mechanism'), {kit, job});
if (!mechanism || typeof mechanism.frame !== 'function') throw Error('mount must return {frame({scene,progress,time})}');
const knowledgeText = text => {
  const points = String(text).split(/\n+|[；;]|\s+[·•]\s+/u).map(point => point.trim()).filter(Boolean);
  // Group only existing caption clauses; this layer never authors new facts.
  if (points.length > 4) points.splice(3, points.length - 3, points.slice(3).join('；'));
  return points.map(point => '• ' + point.replace(/^[•·]\s*/u, '')).join('\n');
};

window.renderFrame = async time => {
  const state = computePresentation(renderJob, timing, time);
  const {scene, index, progress, caption, pane} = state;
  // Mechanisms own causal state; presentation wraps their result without replacing
  // transforms, changing readouts or accumulating a previous frame's movement.
  $('readout').textContent = '';
  await mechanism.frame({scene, progress, time: state.time, reducedMotion: state.reduced_motion, setReadout: text => { $('readout').textContent = text; }});
  $('title').textContent = scene.title;
  $('caption').textContent = knowledgeText(scene.caption);
  $('subtitle').textContent = caption.display_text;
  $('subtitle').style.left = caption.x + '%';
  $('subtitle').style.top = (caption.y + caption.offset_y) + '%';
  $('subtitle').style.width = caption.width + '%';
  $('subtitle').style.height = caption.height + '%';
  $('subtitle').style.textAlign = caption.align;
  $('subtitle').style.fontSize = caption.font_size * caption.font_scale * unit + 'px';
  $('subtitle').style.opacity = String(caption.opacity);
  $('subtitle').dataset.alignment = caption.alignment;
  $('subtitle').dataset.mode = caption.mode;
  $('subtitle').dataset.style = caption.style;
  $('subtitle').dataset.cue = caption.cue?.id || '';
  film.dataset.captionPlacement = caption.placement;
  film.dataset.captionStyle = caption.style;
  film.dataset.reducedMotion = String(state.reduced_motion);
  rail.style.opacity = String(pane.opacity);
  rail.style.transform = `translateY(${pane.offset_y * unit}px)`;
  camera.style.transformOrigin = `${state.camera.origin_x}% ${state.camera.origin_y}%`;
  camera.style.transform = `translate(${state.camera.x}%, ${state.camera.y}%) scale(${state.camera.scale})`;
  $('figure-no').textContent = `FIG. ${String(index + 1).padStart(2, '0')} / ${job.scenes.length}`;
  chapter.textContent = `${String(index + 1).padStart(2, '0')} / ${String(job.scenes.length).padStart(2, '0')}`;
  $('hint').textContent = scene.hint || (job.language.startsWith('zh') ? '机制演示 · 示意模型' : 'MECHANISM · ILLUSTRATIVE MODEL');
  $('source').textContent = (scene.source_ids || []).map(id => job.sources.find(source => source.id === id)?.title || id).join(' · ');
  $('progress').style.width = state.reveal.film * 100 + '%';
  bars.forEach((bar, i) => { bar.style.transform = `scaleX(${i < index ? 1 : i === index ? progress : 0})`; });
  // Public acceptance evidence contains only the current, non-secret view state.
  window.__PRESENTATION_STATE__ = {
    time: state.time, scene_id: scene.id, scene_index: index, progress,
    alignment: state.alignment, estimated: state.estimated, reduced_motion: state.reduced_motion,
    caption: {...caption, bounds: {x: caption.x, y: caption.y + caption.offset_y, width: caption.width, height: caption.height}},
    camera: state.camera, pane, reveal: state.reveal,
  };
  await document.fonts.ready;
};
await window.renderFrame(0);
window.__VIDEO_READY__ = true;
// The renderer owns time. Preview seek is explicit; no wall-clock animation.
window.seekVideo = window.renderFrame;
