import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { layoutCinemaText, buildCinemaTrack, subtitlesToSrt } from '../scripts/subtitles.mjs';

const job = (extra = {}) => ({ language: 'zh-CN', video: { fps: 24 }, ...extra });
const cue = (id, start, end, text = '木牛流马是人力工具。') => ({ id, scene_id: 'mechanism', start, end, text });
const timing = cues => ({ captions: { alignment: 'measured-phrases', cues } });
const width = text => Array.from(text).reduce((sum, char) => sum + (/^[\x00-\x7F]$/u.test(char) ? .5 : 1), 0);
const script = fileURLToPath(new URL('../scripts/subtitles.mjs', import.meta.url));

async function projectFixture(t, cues = [cue('a', 0, 3)]) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'anatomy-cinema-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const jobText = JSON.stringify(job({ tts: { api_key: 'DO-NOT-COPY-SECRET' } }));
  const timingText = JSON.stringify(timing(cues));
  await fs.writeFile(path.join(dir, 'job.json'), jobText);
  await fs.writeFile(path.join(dir, 'timing.json'), timingText);
  return { dir, jobText, timingText };
}

// Layout is a display transform. It never changes source speech or timestamps.
test('Chinese display uses spaces for comma/full stop, preserving question and exclamation marks', () => {
  const result = layoutCinemaText('木牛，流马。真的是机器吗？！');
  assert.equal(result.text, '木牛 流马 真的是机器吗？！');
  assert.equal(result.display_text, result.text);
  assert.equal(result.line_limit, 16);
});

test('Chinese decimal, domains and grouped numbers survive punctuation normalization', () => {
  assert.equal(layoutCinemaText('速度1.5米，访问example.com。数量1,000。').text,
    '速度1.5米 访问example.com 数量1,000');
});

test('Chinese sixteen fullwidth equivalents prefer one line and ASCII spaces count as halfwidth', () => {
  assert.deepEqual(layoutCinemaText('中'.repeat(14) + 'ABCD').lines, ['中'.repeat(14) + 'ABCD']);
  const withSpace = layoutCinemaText('中'.repeat(14) + ' ABCD');
  assert.equal(withSpace.lines.length, 2);
  assert(withSpace.lines.every(line => width(line) <= 16));
  assert.equal(withSpace.overflow, false);
});

test('Chinese semantic comma boundary becomes a clean balanced line break', () => {
  const result = layoutCinemaText('木牛流马不是自动机器，它依然需要人来推动。');
  assert.deepEqual(result.lines, ['木牛流马不是自动机器', '它依然需要人来推动']);
  assert.equal(result.display_text, '木牛流马不是自动机器\n它依然需要人来推动');
});

test('mixed Latin technical names and explicit keep-together terms stay intact', () => {
  const result = layoutCinemaText('这是一段介绍木牛流马与OpenAI模型混排的字幕文本。', 'zh-CN', ['木牛流马', 'OpenAI模型']);
  assert.equal(result.lines.length, 2);
  assert(result.lines.some(line => line.includes('木牛流马')));
  assert(result.lines.some(line => line.includes('OpenAI模型')));
  assert.equal(result.overflow, false);
});

test('a protected term cannot be split just to meet the line limit', () => {
  const phrase = '中'.repeat(17);
  const result = layoutCinemaText(phrase, 'zh-CN', [phrase]);
  assert.deepEqual(result.lines, [phrase]);
  assert.equal(result.overflow, true);
});

test('line breaks avoid dangling opening and leading closing punctuation', () => {
  const result = layoutCinemaText('「木牛流马」的名称需要完整保留，解释运粮工具也需要清晰表达。');
  assert.equal(result.lines.length, 2);
  assert(result.lines.every(line => !/[（(\[【《〈「『“‘{]$/u.test(line)));
  assert(result.lines.every(line => !/^[，。！？、；：,.!?;:）)\]】》〉」』”’}]/u.test(line)));
  assert.equal(result.overflow, false);
});

test('English has a forty-two-character limit, keeps words and punctuation', () => {
  const sentence = 'Let us explain the mechanism without breaking English words.';
  const result = layoutCinemaText(sentence, 'en-US');
  assert.equal(result.text, sentence);
  assert.equal(result.line_limit, 42);
  assert.equal(result.lines.length, 2);
  assert(result.lines.every(line => Array.from(line).length <= 42));
  assert(result.lines.join(' ').includes('mechanism without'));
  assert.equal(result.overflow, false);
});

test('overflow returns at most two lines without deleting text or shrinking', () => {
  const result = layoutCinemaText('中'.repeat(40));
  assert.equal(result.lines.length, 2);
  assert.equal(result.lines.join(''), '中'.repeat(40));
  assert.equal(result.overflow, true);
  assert.equal(layoutCinemaText('x'.repeat(43), 'en-US').overflow, true);
});

test('empty cue has no visible lines', () => {
  const result = layoutCinemaText(' \n ');
  assert.deepEqual(result.lines, []);
  assert.equal(result.overflow, false);
});

test('track rounds frames, trims only previous tail for a two-frame gap and preserves raw sources', () => {
  const raw = [cue('b', 1.02, 2.13, '第二条。'), { ...cue('a', .013, 1.001, '原始，文案。'), detail: { note: 'retain' } }];
  const input = timing(raw), snapshot = JSON.stringify(input);
  const track = buildCinemaTrack(job(), input);
  const [a, b] = track.cues;
  assert.equal(a.id, 'a');
  assert.equal(a.start_frame, 0);
  assert.equal(a.end_frame, 22);
  assert.equal(b.start_frame, 24);
  assert.equal(b.end_frame, 51);
  assert.equal(b.start_frame - a.end_frame, 2);
  assert.equal(a.source_start, .013);
  assert.equal(a.source_end, 1.001);
  assert.equal(a.source_text, '原始，文案。');
  assert.equal(a.text, '原始 文案');
  assert.deepEqual(a.source, raw[1]);
  assert.equal(a.source.detail.note, 'retain');
  assert.equal(JSON.stringify(input), snapshot);
  a.source.detail.note = 'isolated';
  assert.equal(raw[1].detail.note, 'retain');
});

test('track does not extend an already generous gap', () => {
  const track = buildCinemaTrack(job(), timing([cue('a', 0, 1), cue('b', 2, 3)]));
  assert.equal(track.cues[0].end, 1);
  assert.equal(track.cues[1].start, 2);
});

test('impossible two-frame gap is a warning without deleting a cue or moving its start', () => {
  const track = buildCinemaTrack(job(), timing([cue('a', 0, .1), cue('b', .04, 1)]));
  assert.equal(track.cues.length, 2);
  assert.equal(track.cues[0].start_frame, 0);
  assert.equal(track.cues[0].end_frame, 2);
  assert.equal(track.cues[1].start_frame, 1);
  const item = track.issues.find(item => item.code === 'minimum-gap');
  assert.equal(item.severity, 'warning');
});

test('short and long duration, reading speed and overflow produce explicit QC without cue splitting', () => {
  const track = buildCinemaTrack(job(), timing([cue('short', 0, .2, '这条字幕读速太快而且时长太短。'), cue('long', 1, 9, '短文案。'), cue('wide', 10, 16, '中'.repeat(40))]));
  assert.equal(track.cues.length, 3);
  assert(track.issues.some(item => item.cue_id === 'short' && item.code === 'minimum-duration' && item.severity === 'warning'));
  assert(track.issues.some(item => item.cue_id === 'short' && item.code === 'reading-speed' && item.severity === 'warning'));
  assert(track.issues.some(item => item.cue_id === 'long' && item.code === 'maximum-duration'));
  assert(track.issues.some(item => item.cue_id === 'wide' && item.code === 'line-overflow' && item.severity === 'error'));
});

test('reading speed includes spaces and punctuation, with Chinese ASCII halfwidth and English full character units', () => {
  const zh = buildCinemaTrack(job(), timing([cue('a', 0, 1, '中 a?')]));
  assert.equal(zh.cues[0].reading_speed, 2.5);
  const en = buildCinemaTrack(job({ language: 'en-US' }), timing([cue('a', 0, 1, 'One, two!')]));
  assert.equal(en.cues[0].reading_speed, 9);
  assert.equal(en.profile.reading_speed_limit, 20);
});

test('manual alignment and arbitrary call order are reproducible', () => {
  const raw = [cue('a', 0, 2), cue('b', 2, 4)];
  const manual = { captions: { alignment: 'manual', cues: raw } };
  const first = buildCinemaTrack(job(), manual);
  buildCinemaTrack(job(), timing([cue('other', 8, 9)]));
  assert.deepEqual(buildCinemaTrack(job(), manual, [...raw].reverse()), first);
  assert.equal(first.alignment, 'manual');
  assert.equal(buildCinemaTrack(job(), {}).alignment, 'estimated-phrases');
});

test('invalid timing and fps reject structural errors without silently inventing values', () => {
  assert.throws(() => buildCinemaTrack(job({ video: { fps: 0 } }), timing([])), /fps/);
  assert.throws(() => buildCinemaTrack(job(), timing([cue('a', 1, 1)])), /interval/);
  assert.throws(() => buildCinemaTrack(job(), timing([cue('a', -1, 1)])), /interval/);
});

test('SRT uses shared frame-aligned timing and actual line breaks', () => {
  const track = buildCinemaTrack(job(), timing([cue('a', 0, 4, '木牛流马不是自动机器，它依然需要人来推动。'), cue('b', 4, 6, '下一条。')]));
  assert.equal(subtitlesToSrt(track), '1\n00:00:00,000 --> 00:00:03,916\n木牛流马不是自动机器\n它依然需要人来推动\n\n2\n00:00:04,000 --> 00:00:06,000\n下一条\n');
});

test('parsing SRT millisecond timestamps preserves every selected video frame', () => {
  const seconds = stamp => {
    const [h,m,s,ms] = stamp.split(/[:,]/u).map(Number);
    return h*3600+m*60+s+ms/1000;
  };
  for (const fps of [24,25,30,60]) {
    const config=job();config.video={...config.video,fps};
    const track=buildCinemaTrack(config,timing([
      cue('a',.1,1.11,'第一条'),cue('b',1.11,3.071,'第二条'),
      cue('c',4.056,6.312,'第三条'),cue('d',6.912,10.536,'第四条'),
    ]));
    const parsed=subtitlesToSrt(track).trim().split(/\n\n/u).map(block=>{
      const rows=block.split('\n'),[start,end]=rows[1].split(' --> ').map(seconds);
      return {start,end,text:rows.slice(2).join('\n')};
    });
    for(let frame=0;frame<Math.ceil(11*fps);frame++){
      const expected=track.cues.find(c=>frame>=c.start_frame&&frame<c.end_frame)?.display_text||'';
      const actual=parsed.find(c=>frame/fps>=c.start&&frame/fps<c.end)?.text||'';
      assert.equal(actual,expected,`fps=${fps}, frame=${frame}`);
    }
  }
});

test('CLI writes shared SRT and QC, preserves inputs, and excludes unrelated job credentials', async t => {
  const { dir, jobText, timingText } = await projectFixture(t);
  const result = spawnSync(process.execPath, [script, '--project', dir], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(await fs.readFile(path.join(dir, 'job.json'), 'utf8'), jobText);
  assert.equal(await fs.readFile(path.join(dir, 'timing.json'), 'utf8'), timingText);
  const report = await fs.readFile(path.join(dir, 'subtitle-qc.json'), 'utf8');
  assert(!report.includes('DO-NOT-COPY-SECRET'));
  const track = JSON.parse(report);
  assert.equal(await fs.readFile(path.join(dir, 'cinematic.srt'), 'utf8'), subtitlesToSrt(track));
});

test('CLI records overflow errors while allowing callers to inspect the QC report', async t => {
  const { dir } = await projectFixture(t, [cue('a', 0, 3, '中'.repeat(40))]);
  const output = path.join(dir, 'stage', 'narration.srt'), report = path.join(dir, 'stage', 'qc.json');
  const result = spawnSync(process.execPath, [script, '--project', dir, '--out-SRT', output, '--report', report], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert(JSON.parse(await fs.readFile(report, 'utf8')).issues.some(item => item.severity === 'error'));
  assert((await fs.readFile(output, 'utf8')).includes('中'));
});

test('CLI refuses to overwrite an input, including through a symlink', async t => {
  const { dir, jobText } = await projectFixture(t);
  const link = path.join(dir, 'input-link.srt');
  await fs.symlink(path.join(dir, 'job.json'), link);
  for (const destination of [path.join(dir, 'job.json'), link]) {
    const result = spawnSync(process.execPath, [script, '--project', dir, '--out-srt', destination], { encoding: 'utf8' });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /distinct/);
  }
  assert.equal(await fs.readFile(path.join(dir, 'job.json'), 'utf8'), jobText);
});

test('a sub-frame source cue produces a structural error and cannot export invalid SRT', () => {
  const track = buildCinemaTrack(job(), timing([cue('tiny', .001, .002, '短。')]));
  assert.equal(track.cues[0].start_frame, 0);
  assert.equal(track.cues[0].end_frame, 0);
  assert.equal(track.cues[0].source_end, .002);
  assert(track.issues.some(item => item.code === 'invalid-frame-duration' && item.severity === 'error'));
  assert.throws(() => subtitlesToSrt(track), /positive frame-aligned cue durations/);
});

test('CLI preserves existing outputs by default and replaces designated outputs only with force', async t => {
  const { dir } = await projectFixture(t);
  const first = spawnSync(process.execPath, [script, '--project', dir], { encoding: 'utf8' });
  assert.equal(first.status, 0, first.stderr);
  const output = path.join(dir, 'cinematic.srt');
  await fs.writeFile(output, 'reviewable previous subtitle');
  const refused = spawnSync(process.execPath, [script, '--project', dir], { encoding: 'utf8' });
  assert.equal(refused.status, 1);
  assert.match(refused.stderr, /already exists/);
  assert.equal(await fs.readFile(output, 'utf8'), 'reviewable previous subtitle');
  const forced = spawnSync(process.execPath, [script, '--project', dir, '--force'], { encoding: 'utf8' });
  assert.equal(forced.status, 0, forced.stderr);
  assert.match(await fs.readFile(output, 'utf8'), /00:00:00,000/);
});

test('CLI force refuses every protected input name, unsupported output extension and symlink directory', async t => {
  const { dir } = await projectFixture(t);
  for (const name of ['data.json', 'build-manifest.json', 'JOB.JSON', 'brief.json', 'narration-full.wav', 'model.mjs', 'mechanism.mjs', 'index.html', 'runtime.mjs', 'presentation.mjs', 'video.css']) {
    const output = path.join(dir, name);
    const result = spawnSync(process.execPath, [script, '--project', dir, '--report', output, '--force'], { encoding: 'utf8' });
    assert.equal(result.status, 1, name);
    assert.match(result.stderr, /protected project inputs/);
  }
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'anatomy-cinema-outside-'));
  t.after(() => fs.rm(outside, { recursive: true, force: true }));
  await fs.symlink(outside, path.join(dir, 'linked'), 'dir');
  const linked = spawnSync(process.execPath, [script, '--project', dir, '--report', path.join(dir, 'linked', 'qc.json'), '--force'], { encoding: 'utf8' });
  assert.equal(linked.status, 1);
  assert.match(linked.stderr, /symbolic links/);
  const escaped = spawnSync(process.execPath, [script, '--project', dir, '--report', path.join(outside, 'qc.json'), '--force'], { encoding: 'utf8' });
  assert.equal(escaped.status, 1);
  assert.match(escaped.stderr, /inside the project/);
});

test('CLI rejects zero-frame cues without producing invalid subtitle output', async t => {
  const { dir } = await projectFixture(t, [cue('tiny', .001, .002, '短。')]);
  const result = spawnSync(process.execPath, [script, '--project', dir], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /positive frame-aligned cue durations/);
  await assert.rejects(fs.access(path.join(dir, 'cinematic.srt')));
});
