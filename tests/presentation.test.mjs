import test from 'node:test';
import assert from 'node:assert/strict';
import {computePresentation, splitCaptionText} from '../scripts/presentation.mjs';

const job = {
  language: 'zh-CN', video: {width: 1920, height: 1080},
  presentation: {captions: {style: 'kinetic'}},
  scenes: [
    {id: 'lift', title: '把空气向下推', caption: '旋翼加速；升力随之变化', narration: '旋翼把空气向下推，空气也给旋翼向上的力。转速提高，向下推动的空气更多，升力也随之增加。', hold_seconds: 1},
    {id: 'turn', title: '改变左右两侧转速', caption: '两侧转速差', narration: '左右两侧的转速不同，两侧的升力就不同，机身因此倾斜。', hold_seconds: 0},
  ],
};
const timing = {duration: 16, scenes: [{id: 'lift', start: 0, end: 10, duration: 10}, {id: 'turn', start: 10, end: 16, duration: 6}]};
const measured = {...timing, captions: {alignment: 'measured-phrases', cues: [
  {id: 'lift-p001', scene_id: 'lift', start: .3, end: 2.6, text: '旋翼把空气向下推'},
  {id: 'lift-p002', scene_id: 'lift', start: 2.8, end: 5.1, text: '空气也给旋翼向上的力'},
  {id: 'lift-p003', scene_id: 'lift', start: 5.3, end: 7, text: '转速提高，升力增加'},
  {id: 'lift-p004', scene_id: 'lift', start: 7.1, end: 9, text: '这时向下推动的空气更多'},
  {id: 'turn-p001', scene_id: 'turn', start: 10, end: 15.8, text: '两侧转速差让机身倾斜'},
]}};

test('explicit kinetic phrase captions change with time, end before an explicit hold, and select exact scene boundaries', () => {
  const first = computePresentation(job, timing, .5);
  const later = computePresentation(job, timing, 7);
  assert.equal(first.caption.mode, 'phrase');
  assert.notEqual(first.caption.text, later.caption.text);
  assert.notEqual(first.caption.text, job.scenes[0].narration);
  assert.equal(computePresentation(job, timing, 9.5).caption.text, '');
  const boundary = computePresentation(job, timing, 10);
  assert.equal(boundary.scene_id, 'turn');
  assert.equal(boundary.progress, 0);
  assert.equal(computePresentation(job, timing, 9.999).scene_id, 'lift');
  assert.equal(computePresentation(job, timing, -4).time, 0);
  assert.equal(computePresentation(job, timing, 99).scene_id, 'turn');
});

test('measured manual cues retain exact text and times, including genuine gaps', () => {
  for (const cue of measured.captions.cues) {
    const state = computePresentation(job, measured, (cue.start + cue.end) / 2);
    assert.deepEqual(state.caption.cue, cue);
    assert.equal(state.caption.text, cue.text);
    assert.equal(state.alignment, 'measured-phrases');
    assert.equal(state.estimated, false);
  }
  assert.equal(computePresentation(job, measured, 2.6).caption.cue, null);
  assert.equal(computePresentation(job, measured, 2.7).caption.text, '');
  assert.equal(computePresentation(job, measured, 2.8).caption.cue.id, 'lift-p002');
  assert.equal(computePresentation(job, measured, 10).caption.cue.scene_id, 'turn');
});

test('explicit manual alignment preserves supplied cue values and never reports estimated timing', () => {
  const manual = {...measured, captions: {...measured.captions, alignment: 'manual'}};
  const cue = manual.captions.cues[1];
  const state = computePresentation(job, manual, (cue.start + cue.end) / 2);
  assert.deepEqual(state.caption.cue, cue);
  assert.equal(state.caption.text, cue.text);
  assert.equal(state.alignment, 'manual');
  assert.equal(state.caption.alignment, 'manual');
  assert.equal(state.estimated, false);
  assert.equal(state.caption.estimated, false);
  assert.equal(computePresentation(job, manual, 2.7).caption.cue, null);
  assert.equal(computePresentation(job, manual, cue.start).caption.cue.start, cue.start);
});

test('fallback clearly declares estimated phrase timing and stays inside measured scene intervals', () => {
  for (const time of [.01, 2, 4, 6, 8.5, 10.01, 13, 15.9]) {
    const state = computePresentation(job, timing, time);
    assert.equal(state.alignment, 'estimated-phrases');
    assert.equal(state.caption.alignment, 'estimated-phrases');
    assert.equal(state.estimated, true);
    assert.equal(state.caption.estimated, true);
    const track = timing.scenes[state.index];
    assert.ok(state.caption.cue.start >= track.start);
    assert.ok(state.caption.cue.end <= track.end);
  }
  const speechTimed = structuredClone(timing);
  speechTimed.scenes[0].speech_duration = 7.5;
  assert.equal(computePresentation(job, speechTimed, 8).caption.cue, null);
});

test('a random seek recreates the whole view without mutating input or relying on prior frames', () => {
  const before = JSON.stringify({job, measured});
  const times = [.3, 1.7, 3.2, 5.8, 8.9, 10, 14.2, 15.9];
  const snapshots = new Map(times.map(time => [time, JSON.stringify(computePresentation(job, measured, time))]));
  for (const time of [14.2, .3, 8.9, 3.2, 15.9, 1.7, 10, 5.8]) assert.equal(JSON.stringify(computePresentation(job, measured, time)), snapshots.get(time));
  assert.equal(JSON.stringify({job, measured}), before);
  assert.notDeepEqual(computePresentation(job, measured, 1).camera, computePresentation(job, measured, 8).camera);
  assert.throws(() => computePresentation(job, timing, NaN), /finite/);
});

test('cue opacity fades in and out smoothly and short cues get a readable middle', () => {
  assert.equal(computePresentation(job, measured, .3).caption.opacity, 0);
  assert.ok(computePresentation(job, measured, .4).caption.opacity > .49);
  assert.equal(computePresentation(job, measured, 1).caption.opacity, 1);
  assert.ok(computePresentation(job, measured, 2.59).caption.opacity < .01);
  const short = {...timing, captions: {alignment: 'measured-phrases', cues: [{id: 's', scene_id: 'lift', start: 1, end: 1.1, text: '短句'}]}};
  const middle = computePresentation(job, short, 1.05).caption;
  assert.equal(middle.opacity, 1);
  assert.ok(middle.fade_seconds < .05);
});

test('automatic positions change only at cue changes and remain outside the title, model and source areas', () => {
  const placements = new Set();
  for (const time of [1, 3.5, 6, 8]) {
    const state = computePresentation(job, measured, time);
    const caption = state.caption;
    placements.add(caption.placement);
    assert.deepEqual([caption.x, caption.y], [computePresentation(job, measured, time + .1).caption.x, computePresentation(job, measured, time + .1).caption.y]);
    assert.ok(caption.x >= 4.8 && caption.x + caption.width <= 96);
    assert.ok(caption.y >= 58 && caption.y + caption.height + caption.offset_y < 95.5);
    if (caption.placement === 'left') assert.ok(caption.x + caption.width < 34);
    else assert.ok(caption.y > 83.8);
  }
  assert.equal(placements.size, 4);
});

test('scene placement, explicit global override and portrait safe areas are respected', () => {
  const configured = structuredClone(job);
  configured.scenes[0].presentation = {caption_placement: 'top', zoom: 1.08, focus: [.3, .7]};
  const scene = computePresentation(configured, measured, 3);
  assert.equal(scene.caption.placement, 'top');
  assert.ok(scene.caption.y + scene.caption.height + scene.caption.offset_y < 18.5);
  assert.equal(scene.camera.origin_x, 30);
  assert.equal(scene.camera.origin_y, 70);
  configured.presentation = {captions: {style: 'kinetic', placement: 'right'}};
  assert.equal(computePresentation(configured, measured, 3).caption.placement, 'right');
  configured.video = {width: 1080, height: 1920};
  for (const placement of ['left', 'bottom', 'right', 'top']) {
    configured.presentation.captions.placement = placement;
    const {caption} = computePresentation(configured, measured, 3);
    assert.ok(caption.x >= 5 && caption.x + caption.width <= 95);
    assert.ok(caption.y + caption.height + caption.offset_y < 94);
    if (placement !== 'top') assert.ok(caption.y > 75.5);
    else assert.ok(caption.y >= 25.5 && caption.y + caption.height < 35.5);
  }
});

test('long phrases wrap in two lines while English words and manual cue values remain intact', () => {
  const chinese = '旋翼旋转以后持续向下推动空气并且获得向上的反作用力从而使得整个机身升起';
  assert.ok(splitCaptionText(chinese).every(text => Array.from(text).length <= 32));
  const english = 'Each rotor transfers momentum to the surrounding air, and the reaction force lifts the aircraft. Counterclockwise rotation balances clockwise rotation.';
  const phrases = splitCaptionText(english);
  for (const word of english.match(/[A-Za-z]+/g)) assert.ok(phrases.some(phrase => phrase.includes(word)), word);
  const manual = {...timing, captions: {alignment: 'measured-phrases', cues: [{id: 'verbatim', scene_id: 'lift', start: .25, end: 4.75, text: chinese}]}};
  const caption = computePresentation(job, manual, 2).caption;
  assert.equal(caption.text, chinese);
  assert.equal(caption.lines.length, 2);
  assert.equal(caption.lines.join(''), chinese);
  assert.deepEqual(caption.cue, manual.captions.cues[0]);
});

test('legacy scene mode retains the complete narration across the scene interval', () => {
  const legacy = {...job, presentation: {captions: {mode: 'scene'}}};
  for (const time of [0, 2.7, 9.9]) {
    const state = computePresentation(legacy, measured, time);
    assert.equal(state.alignment, 'scene');
    assert.equal(state.estimated, false);
    assert.equal(state.caption.mode, 'scene');
    assert.equal(state.caption.text, job.scenes[0].narration);
    assert.equal(state.caption.opacity, 1);
  }
  assert.equal(computePresentation(legacy, measured, 10).caption.text, job.scenes[1].narration);
});

test('reduced motion keeps every active phrase fully readable and removes presentation motion', () => {
  const reduced = {...job, presentation: {captions: {style: 'kinetic'}, reduced_motion: true}};
  const state = computePresentation(reduced, measured, .3);
  assert.equal(state.caption.opacity, 1);
  assert.equal(state.caption.offset_y, 0);
  assert.equal(state.pane.opacity, 1);
  assert.equal(state.pane.offset_y, 0);
  assert.deepEqual(state.camera, {x: 0, y: 0, scale: 1, origin_x: 50, origin_y: 50});
});


const cinemaJob = {...job, video: {...job.video, fps: 24}, presentation: {captions: {mode: 'phrase', placement: 'auto'}}};

test('cinema is the default even for old auto placement and stays full-frame centered', () => {
  const states = [1, 3.5, 6, 8].map(time => computePresentation(cinemaJob, measured, time));
  for (const {caption} of states) {
    assert.equal(caption.style, 'cinema');
    assert.equal(caption.placement, 'bottom');
    assert.equal(caption.align, 'center');
    assert.equal(caption.x + caption.width / 2, 50);
    assert.equal(caption.font_size, 42);
    assert.equal(caption.font_scale, 1);
    assert.equal(caption.offset_y, 0);
    assert.equal(caption.fade_seconds, 0);
    assert.equal(caption.opacity, 1);
  }
  const {caption: first} = states[0];
  for (const {caption} of states.slice(1)) assert.deepEqual(
    [caption.x, caption.y, caption.width, caption.height],
    [first.x, first.y, first.width, first.height]);
  const explicitPosition = structuredClone(cinemaJob);
  explicitPosition.presentation.captions.placement = 'right';
  explicitPosition.scenes[0].presentation = {caption_placement: 'left'};
  assert.equal(computePresentation(explicitPosition, measured, 1).caption.placement, 'bottom');
});

test('cinema cuts directly on derived half-open frame boundaries and preserves original source evidence', () => {
  const source = {id: 'raw', scene_id: 'lift', start: .3, end: 3.3, text: '旋翼向下推，空气向上托。'};
  const sourceTiming = {...timing, captions: {alignment: 'manual', cues: [source]}};
  const snapshot = JSON.stringify({cinemaJob, sourceTiming});
  const middle = computePresentation(cinemaJob, sourceTiming, 1).caption;
  assert.deepEqual(middle.cue, source);
  assert.equal(middle.source_text, source.text);
  assert.equal(middle.source_start, source.start);
  assert.equal(middle.source_end, source.end);
  assert.equal(middle.display_text, middle.lines.join('\n'));
  assert.equal(middle.text.includes('，'), false);
  assert.equal(middle.text.includes('。'), false);
  assert.equal(middle.opacity, 1);
  assert.equal(middle.fade_seconds, 0);
  assert.equal(middle.start * 24, middle.start_frame);
  assert.equal(middle.end * 24, middle.end_frame);
  assert.equal(computePresentation(cinemaJob, sourceTiming, middle.start).caption.opacity, 1);
  assert.equal(computePresentation(cinemaJob, sourceTiming, middle.end).caption.opacity, 0);
  assert.equal(computePresentation(cinemaJob, sourceTiming, middle.end).caption.cue, null);
  assert.equal(JSON.stringify({cinemaJob, sourceTiming}), snapshot);
});

test('one and two cinema lines retain a common baseline, font size and theme-independent placement', () => {
  const one = {id: 'one', scene_id: 'lift', start: .3, end: 3, text: '向下推空气'};
  const two = {id: 'two', scene_id: 'lift', start: 4, end: 8, text: '旋翼持续向下推动周围空气并获得方向相反的向上作用力'};
  const sourceTiming = {...timing, captions: {alignment: 'measured-phrases', cues: [one, two]}};
  const a = computePresentation(cinemaJob, sourceTiming, 1).caption;
  const b = computePresentation(cinemaJob, sourceTiming, 5).caption;
  assert.equal(a.lines.length, 1);
  assert.equal(b.lines.length, 2);
  assert.equal(a.y + a.height, b.y + b.height);
  assert.equal(a.font_scale, 1);
  assert.equal(b.font_scale, 1);
  assert.equal(b.font_size, a.font_size);
  for (const theme of ['anatomy-official-light', 'anatomy-official-dark']) {
    assert.deepEqual(computePresentation({...cinemaJob, theme}, sourceTiming, 5).caption, b);
  }
});

test('cinema portrait subtitles keep stable lower safety margins and never use kinetic positions', () => {
  const portrait = {...cinemaJob, video: {width: 1080, height: 1920, fps: 24}};
  for (const time of [1, 3.5, 6, 8]) {
    const {caption} = computePresentation(portrait, measured, time);
    assert.equal(caption.style, 'cinema');
    assert.equal(caption.x + caption.width / 2, 50);
    assert.ok(caption.x >= 7 && caption.x + caption.width <= 93);
    assert.ok(caption.y >= 78 && caption.y + caption.height <= 92);
    assert.equal(caption.font_scale, 1);
    assert.equal(caption.opacity, 1);
    assert.equal(caption.offset_y, 0);
  }
});

test('cinema estimated fallback remains honestly labeled and still clears a deliberate scene hold', () => {
  const active = computePresentation(cinemaJob, timing, 2);
  assert.equal(active.alignment, 'estimated-phrases');
  assert.equal(active.caption.style, 'cinema');
  assert.equal(active.caption.estimated, true);
  assert.equal(active.caption.qc.profile.max_lines, 2);
  assert.equal(computePresentation(cinemaJob, timing, 9.5).caption.text, '');
});

test('cinema overflow remains visible to quality control rather than silently shrinking text', () => {
  const source = {id: 'long', scene_id: 'lift', start: .3, end: 8.5, text: '持续推动空气的旋翼使得整个机身能够上升并且通过调节各个旋翼的转速使不同方向的升力产生变化从而实现倾斜和前进'};
  const sourceTiming = {...timing, captions: {alignment: 'manual', cues: [source]}};
  const {caption} = computePresentation(cinemaJob, sourceTiming, 2);
  assert.equal(caption.font_scale, 1);
  assert.deepEqual(caption.cue, source);
  assert.equal(caption.qc.overflow, true);
  assert.ok(caption.qc.issues.length > 0);
});


test('cinema queries frame-rounded cues globally across non-frame-aligned scene boundaries', () => {
  const crossingTiming = {duration: 16, scenes: [
    {id: 'lift', start: 0, end: 10.02, duration: 10.02},
    {id: 'turn', start: 10.02, end: 16, duration: 5.98},
  ], captions: {alignment: 'manual', cues: [
    {id: 'before', scene_id: 'lift', start: .3, end: 9, text: '先解释旋翼'},
    {id: 'after', scene_id: 'turn', start: 10.02, end: 15.5, text: '然后解释转向'},
  ]}};
  // 10.02 rounds back to frame 240 (10s), while the mechanism remains in lift.
  const next = computePresentation(cinemaJob, crossingTiming, 10);
  assert.equal(next.scene_id, 'lift');
  assert.equal(next.caption.cue.scene_id, 'turn');
  assert.equal(next.caption.start_frame, 240);
  assert.equal(next.caption.opacity, 1);
  assert.equal(computePresentation(cinemaJob, crossingTiming, 239 / 24).caption.cue, null);

  const tailTiming = structuredClone(crossingTiming);
  tailTiming.scenes[0].end = tailTiming.scenes[1].start = 10.03;
  tailTiming.captions.cues[0].end = 10.03;
  tailTiming.captions.cues[1].start = 12;
  // 10.03 rounds forward to frame 241: the old subtitle still owns frame 240.
  const previous = computePresentation(cinemaJob, tailTiming, 10.03);
  assert.equal(previous.scene_id, 'turn');
  assert.equal(previous.caption.cue.scene_id, 'lift');
  assert.equal(previous.caption.end_frame, 241);
  assert.equal(previous.caption.opacity, 1);
  assert.equal(computePresentation(cinemaJob, tailTiming, 241 / 24).caption.cue, null);
});
