import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { assertOutputSafe, parseArgs, resolveRequestPath, validateConfig, validateNarrationProbe, verifyBuildManifest } from '../scripts/render.mjs';

test('video configuration rounds duration to an exact frame timeline', () => {
  assert.deepEqual(validateConfig({ video: { width: 1920, height: 1080, fps: 30 } }, { duration: 1.01, scenes: [] }), { width: 1920, height: 1080, fps: 30, frameCount: 31, duration: 31 / 30, requestedDuration: 1.01 });
  assert.throws(() => validateConfig({ video: { width: 1919, height: 1080, fps: 30 } }, { duration: 1 }), /even integer/);
  assert.throws(() => validateConfig({ video: { width: 1920, height: 1080, fps: 0 } }, { duration: 1 }), /fps/);
  assert.throws(() => validateConfig({ video: { width: 1920, height: 1080, fps: 30 } }, { duration: 0 }), /duration/);
});

test('CLI prevents ambiguous frame and output combinations', () => {
  assert.deepEqual(parseArgs(['--project', 'demo', '--frame', '3', '--still', 'preview.png']), { project: 'demo', frame: 3, still: 'preview.png', force: false });
  assert.throws(() => parseArgs(['--project', 'demo', '--frame', '3']), /together/);
  assert.throws(() => parseArgs(['--project', 'demo', '--frame', '-1', '--still', 'p.png']), /nonnegative/);
  assert.throws(() => parseArgs(['--project', 'demo', '--frame', '0', '--still', 'p.png', '--out', 'v.mp4']), /--out/);
  assert.throws(() => parseArgs(['--project', 'demo', '--out', 'index.html']), /\.mp4/);
  assert.throws(() => parseArgs(['--project', 'demo', '--project', 'other']), /Duplicate/);
});

test('HTTP resource resolution permits local assets and rejects traversal and symlink escapes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'anatomy-route-test-'));
  try {
    const project = join(root, 'project'); await mkdir(project);
    await writeFile(join(project, 'index.html'), 'ok');
    await writeFile(join(project, '图.png'), 'ok');
    await writeFile(join(root, 'private.txt'), 'secret');
    await symlink(join(root, 'private.txt'), join(project, 'escape.txt'));
    assert.equal(await resolveRequestPath(project, '/?v=1'), await realpath(join(project, 'index.html')));
    assert.equal(await resolveRequestPath(project, '/%E5%9B%BE.png'), await realpath(join(project, '图.png')));
    for (const path of ['/../private.txt', '/%2e%2e/private.txt', '/foo/../../private.txt', '/%2f..%2fprivate.txt', '//etc/passwd', '/foo%5c..%5cprivate.txt', '/escape.txt', '/%00', '/%ZZ']) await assert.rejects(resolveRequestPath(project, path));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('force never allows overwriting a known input or its symlink', async () => {
  const project = await mkdtemp(join(tmpdir(), 'anatomy-output-test-'));
  try {
    const asset = join(project, 'asset.png'); const output = join(project, 'preview.png');
    await writeFile(asset, 'input'); await writeFile(output, 'old');
    await symlink(asset, join(project, 'alias.png'));
    await assert.rejects(assertOutputSafe(asset, new Set([asset]), true), /project input/);
    await assert.rejects(assertOutputSafe(join(project, 'alias.png'), new Set([asset]), true), /project input/);
    await assert.rejects(assertOutputSafe(output, new Set([asset])), /already exists/);
    assert.equal(await assertOutputSafe(output, new Set([asset]), true), output);
  } finally { await rm(project, { recursive: true, force: true }); }
});

test('build manifest requires all fixed input hashes and rejects stale builds', async () => {
  const project = await mkdtemp(join(tmpdir(), 'anatomy-manifest-test-'));
  try {
    assert.equal(await verifyBuildManifest(project), false);
    const manifest = { schema_version: 1, inputs: {} };
    for (const name of ['job.json', 'timing.json', 'data.json']) {
      const bytes = Buffer.from(`{"name":"${name}"}\n`);
      await writeFile(join(project, name), bytes);
      manifest.inputs[name] = createHash('sha256').update(bytes).digest('hex');
    }
    const save = async () => writeFile(join(project, 'build-manifest.json'), JSON.stringify(manifest));
    await save();
    assert.equal(await verifyBuildManifest(project), true);
    await writeFile(join(project, 'timing.json'), '{}');
    await assert.rejects(verifyBuildManifest(project), /changed since the build/);
    delete manifest.inputs['timing.json']; await save();
    await assert.rejects(verifyBuildManifest(project), /schema or input set/);
    manifest.inputs['timing.json'] = 'a'.repeat(63); await save();
    await assert.rejects(verifyBuildManifest(project), /complete SHA-256/);
    manifest.inputs['timing.json'] = 'a'.repeat(64); manifest.inputs['../secret.json'] = 'a'.repeat(64); await save();
    await assert.rejects(verifyBuildManifest(project), /schema or input set/);
  } finally { await rm(project, { recursive: true, force: true }); }
});

test('narration must contain audio and match the unrounded timeline duration', () => {
  const probe = (duration) => ({ streams: [{ codec_type: 'audio', duration: String(duration) }], format: { duration: String(duration) } });
  assert.equal(validateNarrationProbe(probe(1.007), 1), 1.007);
  assert.equal(validateNarrationProbe({ streams: [{ codec_type: 'audio' }], format: { duration: '1' } }, 1), 1);
  assert.throws(() => validateNarrationProbe(probe(1.02), 1), /does not match/);
  assert.throws(() => validateNarrationProbe(probe(0.9), 1), /does not match/);
  assert.throws(() => validateNarrationProbe({ streams: [{ codec_type: 'video' }], format: { duration: '1' } }, 1), /audio stream/);
});
