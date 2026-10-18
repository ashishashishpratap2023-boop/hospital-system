import test from 'node:test';
import assert from 'node:assert/strict';
import { seal, unseal } from './authstore.js';
import { Pacer } from './pacer.js';

const K = 'k'.repeat(40);
test('session data is encrypted and round-trips, including Buffers', () => {
  const obj = { noiseKey: { private: Buffer.from('secret-bytes') }, id: 7 };
  const sealed = seal(obj, K);
  assert.ok(!sealed.toString('utf8').includes('secret'));
  assert.deepEqual(unseal(sealed, K).noiseKey.private, Buffer.from('secret-bytes'));
  assert.throws(() => unseal(sealed, 'x'.repeat(40)));
  const tampered = Buffer.from(sealed); tampered[tampered.length - 1] ^= 1;
  assert.throws(() => unseal(tampered, K));
});
test('pacer sends sequentially with gaps and enforces limits', async () => {
  const gaps = []; const p = new Pacer({ minGap: 100, jitter: 0, maxPerDay: 3, maxQueue: 2, sleep: async (ms) => gaps.push(ms) });
  const order = [];
  const a = p.run(async () => { order.push('a'); });
  const b = p.run(async () => { order.push('b'); });
  await assert.rejects(p.run(async () => {}), { message: 'queue_full' });
  await Promise.all([a, b]);
  assert.deepEqual(order, ['a', 'b']);
  await p.run(async () => {});
  await assert.rejects(p.run(async () => {}), { message: 'daily_limit' });
  assert.ok(gaps.length >= 2 && gaps.every((g) => g === 100));
});
