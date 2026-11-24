import assert from 'node:assert/strict';
import { test } from 'node:test';
import { classifyFailure, dietQueueItem, drainQueue, generateKey, parseDietBody } from '../src/lib/retry-queue.ts';

function memoryStore(seed = []) {
  let nextId = 1;
  const rows = seed.map(item => ({ ...item, id: nextId++ }));
  return {
    rows,
    async list() { return rows.map(r => ({ ...r })); },
    async insert(item) { const id = nextId++; rows.push({ ...item, id }); return id; },
    async remove(id) { const i = rows.findIndex(r => r.id === id); if (i >= 0) rows.splice(i, 1); },
    async bump(id, attempts, error) { const row = rows.find(r => r.id === id); if (row) { row.attempts = attempts; row.lastError = error; } },
  };
}

function item(overrides = {}) {
  return { kind: 'diet', petId: 1, method: 'POST', path: '/pets/1/diet', body: '{"food_type":"dry","amount_g":50}',
           idempotencyKey: 'k1', attempts: 0, createdAt: 1000, lastError: null, ...overrides };
}

test('generateKey produces 32 lowercase hex and varies with rand', () => {
  let seed = 12345;
  const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  const a = generateKey(rand);
  const b = generateKey(rand);
  assert.match(a, /^[0-9a-f]{32}$/);
  assert.notEqual(a, b);
});

test('classifyFailure: network errors and 5xx/408/429 retry', () => {
  assert.equal(classifyFailure(new TypeError('Network request failed')), 'retry');
  assert.equal(classifyFailure({ status: 500 }), 'retry');
  assert.equal(classifyFailure({ status: 503 }), 'retry');
  assert.equal(classifyFailure({ status: 408 }), 'retry');
  assert.equal(classifyFailure({ status: 429 }), 'retry');
});

test('classifyFailure: expired token keeps the item for retry', () => {
  for (const status of [401, 403]) {
    assert.equal(classifyFailure({ status, message: 'x' }), 'retry');
  }
});

test('classifyFailure: other 4xx drop', () => {
  for (const status of [400, 404, 409, 422]) {
    assert.equal(classifyFailure({ status, message: 'x' }), 'drop');
  }
});

test('drainQueue syncs all in insertion order and empties store', async () => {
  const store = memoryStore([item({ idempotencyKey: 'k1' }), item({ idempotencyKey: 'k2' })]);
  const seen = [];
  const result = await drainQueue(store, async it => { seen.push(it.idempotencyKey); });
  assert.deepEqual(seen, ['k1', 'k2']);
  assert.equal(result.synced, 2);
  assert.equal(result.remaining, 0);
  assert.equal(result.stopped, false);
  assert.deepEqual(result.dropped, []);
});

test('drainQueue stops at first retryable failure and bumps attempts', async () => {
  const store = memoryStore([item({ idempotencyKey: 'k1' }), item({ idempotencyKey: 'k2' })]);
  const result = await drainQueue(store, async it => {
    if (it.idempotencyKey === 'k1') throw new TypeError('Network request failed');
  });
  assert.equal(result.synced, 0);
  assert.equal(result.stopped, true);
  assert.equal(result.remaining, 2);
  assert.equal(store.rows[0].attempts, 1);
  assert.equal(store.rows[0].lastError, 'Network request failed');
});

test('drainQueue drops non-retryable failures and continues', async () => {
  const store = memoryStore([item({ idempotencyKey: 'bad' }), item({ idempotencyKey: 'good' })]);
  const result = await drainQueue(store, async it => {
    if (it.idempotencyKey === 'bad') throw Object.assign(new Error('克数需大于 0'), { status: 400 });
  });
  assert.equal(result.synced, 1);
  assert.equal(result.dropped.length, 1);
  assert.equal(result.dropped[0].item.idempotencyKey, 'bad');
  assert.equal(result.dropped[0].error, '克数需大于 0');
  assert.equal(result.remaining, 0);
});

test('drainQueue resumes after network recovers without duplicating synced items', async () => {
  const store = memoryStore([item()]);
  let online = false;
  const send = async () => { if (!online) throw new TypeError('offline'); };
  await drainQueue(store, send);
  assert.equal(store.rows.length, 1);
  online = true;
  const second = await drainQueue(store, send);
  assert.equal(second.synced, 1);
  assert.equal(store.rows.length, 0);
});

test('dietQueueItem builds POST path and JSON body with key', () => {
  const built = dietQueueItem(7, { food_type: 'wet', amount_g: 80 }, 'key-9', 12345);
  assert.equal(built.kind, 'diet');
  assert.equal(built.petId, 7);
  assert.equal(built.method, 'POST');
  assert.equal(built.path, '/pets/7/diet');
  assert.equal(built.idempotencyKey, 'key-9');
  assert.equal(built.createdAt, 12345);
  assert.deepEqual(JSON.parse(built.body), { food_type: 'wet', amount_g: 80 });
});

test('parseDietBody reads fields and falls back fed_at to createdAt', () => {
  const parsed = parseDietBody(item({ body: '{"food_type":"snack","amount_g":10,"note":"奖励"}', createdAt: Date.UTC(2026, 10, 19, 8) }));
  assert.equal(parsed.food_type, 'snack');
  assert.equal(parsed.amount_g, 10);
  assert.equal(parsed.note, '奖励');
  assert.equal(parsed.fed_at, new Date(Date.UTC(2026, 10, 19, 8)).toISOString());
  const explicit = parseDietBody(item({ body: '{"food_type":"dry","amount_g":50,"fed_at":"2026-11-19T00:00:00Z"}' }));
  assert.equal(explicit.fed_at, '2026-11-19T00:00:00Z');
});

test('drainQueue does not bump when remove fails after a successful send', async () => {
  const store = memoryStore([item()]);
  store.remove = async () => { throw new Error('disk full'); };
  const result = await drainQueue(store, async () => {});
  assert.equal(result.synced, 1);
  assert.equal(store.rows[0].attempts, 0);
});

test('drainQueue drops a repeatedly failing 5xx once the attempt budget is spent', async () => {
  const store = memoryStore([item({ attempts: 7 })]);
  const result = await drainQueue(store, async () => { throw Object.assign(new Error('boom'), { status: 500 }); });
  assert.equal(result.synced, 0);
  assert.equal(result.dropped.length, 1);
  assert.equal(result.dropped[0].error, 'boom');
  assert.equal(store.rows.length, 0);
});

test('drainQueue never drops a pure network failure, however many attempts', async () => {
  const store = memoryStore([item({ attempts: 99 })]);
  const result = await drainQueue(store, async () => { throw new TypeError('offline'); });
  assert.equal(result.stopped, true);
  assert.equal(store.rows.length, 1);
  assert.equal(store.rows[0].attempts, 100);
});

test('drainQueue shares one in-flight run per store', async () => {
  const store = memoryStore([item()]);
  let sends = 0;
  const send = async () => { sends += 1; await new Promise(resolve => setTimeout(resolve, 20)); };
  const [a, b] = await Promise.all([drainQueue(store, send), drainQueue(store, send)]);
  assert.equal(sends, 1);
  assert.equal(a.synced, 1);
  assert.equal(b.synced, 1);
});
