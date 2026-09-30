const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/modules/recordings.js'), 'utf8')
  .replace(/^import .*;$/gm, '').replaceAll('export function', 'function');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
class Element {
  constructor(tag, props = {}) { this.tag = tag; this.children = []; this.events = {}; this.value = ''; Object.assign(this, props); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  set innerHTML(value) { this.html = value; this.children = []; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(key, callback) { this.events[key] = callback; }
  fire(key) { return this.events[key]?.({preventDefault() {}, stopPropagation() {}}); }
}
const walk = node => [node, ...node.children.flatMap(walk)];
function harness() {
  const timers = new Map(), requests = []; let next = 0, disposed = 0;
  const el = (tag, props, ...children) => { const node = new Element(tag, props); node.append(...children); return node; };
  const scope = {
    el, t: key => key, svgIcon: () => '', state: {cameras: [], rec: {page: 0, pageSize: 50}},
    api: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
    createRecordingPlayback: () => ({dispose() { disposed++; }, select() {}}),
    setTimeout: (fn, delay) => { assert.equal(delay, 15000); timers.set(++next, fn); return next; },
    clearTimeout: id => timers.delete(id),
  };
  const view = new Function(...Object.keys(scope), source + ';return {renderRecordings, stopRecordings};')(...Object.values(scope));
  const root = el('div'); view.renderRecordings(root);
  return {...view, requests, timers, disposed: () => disposed,
    find: predicate => walk(root).find(predicate),
    search: () => walk(root).find(node => node.tag === 'button' && node.className === 'btn-primary').fire('click')};
}
const empty = {total: 0, retention_days: 7, items: []};
(async () => {
  {
    const h = harness(); [...h.timers.values()][0]();
    assert(h.requests[0].options.signal.aborted);
    h.requests[0].reject(new DOMException('Timeout', 'AbortError')); await flush();
    assert(h.find(node => node.textContent === 'rec.loadFailed')); assert.equal(h.requests.length, 1);
    h.search(); h.requests[1].resolve(empty); await flush();
    assert(h.find(node => node.textContent === 'rec.none')); assert.equal(h.timers.size, 0);
    h.stopRecordings();
  }
  {
    const h = harness(); h.search();
    assert(h.requests[0].options.signal.aborted); assert.equal(h.timers.size, 1);
    h.requests[0].reject(new Error('Old response')); await flush();
    assert(!h.find(node => node.textContent === 'rec.loadFailed')); assert.equal(h.timers.size, 1);
    h.requests[1].resolve(empty); await flush(); assert.equal(h.timers.size, 0); h.stopRecordings();
  }
  {
    const h = harness(); h.stopRecordings();
    assert(h.requests[0].options.signal.aborted); assert.equal(h.timers.size, 0);
    h.requests[0].resolve(empty); await flush(); h.search();
    assert(!h.find(node => node.textContent === 'rec.none')); assert.equal(h.requests.length, 1);
    assert.equal(h.disposed(), 1);
  }
  {
    const h = harness(); h.requests[0].reject(new Error('Network')); await flush();
    assert(h.find(node => node.textContent === 'rec.loadFailed')); assert.equal(h.timers.size, 0);
    h.stopRecordings();
  }
  console.log('Recordings list timeout, supersession and cleanup contracts passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
