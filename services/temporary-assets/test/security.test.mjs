import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { inspectPng } from '../src/worker.mjs';

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
