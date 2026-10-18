// Sends one message at a time with a human-like gap and a daily cap, to reduce the risk of the number being restricted.
export class Pacer {
  constructor({ minGap = 4000, jitter = 4000, maxPerDay = 250, maxQueue = 10, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
    Object.assign(this, { minGap, jitter, maxPerDay, maxQueue, sleep, tail: Promise.resolve(), queued: 0, day: null, count: 0 });
  }
  run(fn) {
    const err = (m) => Promise.reject(Object.assign(new Error(m), { code: 429 }));
    if (this.queued >= this.maxQueue) return err('queue_full');
    const today = new Date().toISOString().slice(0, 10);
    if (this.day !== today) { this.day = today; this.count = 0; }
    if (this.count >= this.maxPerDay) return err('daily_limit');
    this.count++; this.queued++;
    const p = this.tail.then(async () => { try { return await fn(); } finally { this.queued--; } });
    this.tail = p.catch(() => {}).then(() => this.sleep(this.minGap + Math.random() * this.jitter));
    return p;
  }
}
