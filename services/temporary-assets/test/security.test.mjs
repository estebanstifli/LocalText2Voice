import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { inspectPng, inspectWav } from '../src/worker.mjs';

test('WAV validation accepts bounded mono PCM and rejects malformed or overlong audio', () => {
  const wav = seconds => {
    const data = Buffer.alloc(44 + 24000 * 2 * seconds);
    data.write('RIFF'); data.writeUInt32LE(data.length - 8, 4); data.write('WAVEfmt ', 8);
    data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
    data.writeUInt32LE(24000, 24); data.writeUInt32LE(48000, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34);
    data.write('data', 36); data.writeUInt32LE(data.length - 44, 40); return data;
  };
  assert.equal(inspectWav(wav(14)).seconds, 14);
  for (const bad of [wav(16), wav(2), wav(4).subarray(0, 50), Buffer.from('<script>')]) assert.throws(() => inspectWav(bad));
  const stereo = wav(4); stereo.writeUInt16LE(2, 22); assert.throws(() => inspectWav(stereo));
});

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQAY3Y2wAAAAAElFTkSuQmCC', 'base64');

test('only complete, bounded RGB/RGBA PNG containers are accepted', () => {
  assert.deepEqual(inspectPng(png), { width: 1, height: 1 });
  for (const bad of [Buffer.from('<html>bad</html>'), png.subarray(0, -1), Buffer.concat([png, Buffer.from('hidden')])]) {
    assert.throws(() => inspectPng(bad));
  }
  const oversized = Buffer.from(png);
  oversized.writeUInt32BE(5000, 16);
  assert.throws(() => inspectPng(oversized), /dimensions/);
});

test('public requests cannot invoke the internal cleanup route', async () => {
  const response = await worker.fetch(new Request('https://storage.example/internal/cleanup'), {});
  assert.equal(response.status, 404);
});

test('health response is public and cannot reveal credentials or enable caching', async () => {
  const response = await worker.fetch(new Request('https://storage.example/health'), {});
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.deepEqual(await response.json(), { service: 'localtext2voice-temporary-assets', version: 1 });
});
