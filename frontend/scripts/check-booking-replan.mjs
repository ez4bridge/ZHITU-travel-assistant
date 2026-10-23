// Run the real booking editor setup with in-memory adapters and localStorage.
// This verifies regenerate-and-save without a browser, network request, model,
// map provider, booking supplier or saved database.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'

const root = fileURLToPath(new URL('../', import.meta.url))
const original = {
  id: 'old-plan', request: { origin: '长沙', destination: '上海', destinations: [{ city: '上海' }], start_date: '2026-10-28', days: 3 },
  itinerary: { transfers: [], days: [{ date: '2026-10-28', stops: [
    { id: 'sight', name: '外滩', kind: 'sight', start: '14:00', end: '15:00' },
  ] }] }, validation: { total_cents: 180000 },
}
const initialBytes = JSON.stringify(original)
const hotel = { city: '上海', name: '如家酒店', address: '柳营路518号', check_in: '2026-10-28', check_out: '2026-10-30', total_cost: 600 }
const train = { from_city: '长沙', to_city: '上海', mode: 'train', departure_date: '2026-10-28', departure_time: '09:20', arrival_date: '2026-10-28', arrival_time: '13:21', number: 'G232', departure_place: '长沙南站', arrival_place: '上海站', total_cost: 1144 }
const context = { base_fingerprint: 'a'.repeat(64), expected_routing_revision: 4, expected_state_revision: 7,
  current_plan_id: original.id, confirmed_hotels: [], booked_transports: [] }
const draftKey = id => `booking-draft-v1:${id}`
const clone = value => JSON.parse(JSON.stringify(value))

// Capture emits and provide storage without changing the shared UI harness.
// Application modules are compiled from their real current source files.
function harness(storage = new Map()) {
  const cache = new Map(), cleanups = [], events = [], props = vue.reactive({ plan: clone(original) })
  const scope = vue.effectScope()
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename)
    let source = readFileSync(filename, 'utf8')
    if (filename.endsWith('.vue')) {
      const { descriptor } = parse(source, { filename })
      source = compileScript(descriptor, { id: 'booking-replan-check' }).content
    }
    const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
    const sandbox = {
      exports: {}, console, Error, AbortController, DOMException,
      localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key) },
      fetch() { throw new Error('Network is forbidden in the booking regression harness') },
      require(name) {
        if (name === 'vue') return { ...vue, onBeforeUnmount: fn => cleanups.push(fn) }
        if (name === 'lucide-vue-next' || name.endsWith('.vue')) return new Proxy({}, { get: () => ({}) })
        if (name.endsWith('/api')) return { api() { throw new Error('Unexpected application API request') } }
        return load(path.resolve(path.dirname(filename), name + (path.extname(name) ? '' : '.ts')))
      },
    }
    vm.runInNewContext(output, sandbox, { filename })
    cache.set(filename, sandbox.exports)
    return sandbox.exports
  }
  const adapter = load(path.join(root, 'src/bookingApi.ts'))
  let obsoleteCalls = 0
  adapter.getBookingContext = async () => clone(context)
  adapter.previewBookings = adapter.applyBookings = async () => { obsoleteCalls++; throw new Error('Obsolete preview/apply API must not be called') }
  const editor = scope.run(() => load(path.join(root, 'src/components/BookingEditor.vue')).default.setup(props,
    { expose() {}, emit: (...args) => events.push(args) }))
  return { editor, adapter, events, props, storage, obsoleteCalls: () => obsoleteCalls,
    cleanup() { cleanups.forEach(fn => fn()); scope.stop() } }
}
function fill(editor) {
  editor.hotels.value = [{ ...hotel }]
  editor.transports.value = [{ ...train }]
  editor.confirmed.value = true
}
let groups = 0
async function check(name, fn) { await fn(); groups++; console.log(`PASS ${name}`) }

await check('one regeneration sends fixed facts and CAS revisions; applied closes and emits the saved plan', async () => {
  const t = harness(), { editor, adapter } = t
  try {
    await editor.open(); fill(editor)
    assert(t.storage.has(draftKey(original.id)))
    let resolve, calls = 0
    adapter.regenerateBookings = (id, body, signal) => {
      calls++
      assert.equal(id, original.id)
      assert.equal(body.booked_transports[0].arrival_place, '上海站')
      assert.equal(body.confirmed_hotels[0].address, hotel.address)
      assert.equal(body.base_fingerprint, context.base_fingerprint)
      assert.equal(body.expected_routing_revision, 4)
      assert.equal(body.expected_state_revision, 7)
      assert.equal(signal.aborted, false)
      return new Promise(done => { resolve = done })
    }
    const pending = editor.regenerate()
    assert.equal(editor.working.value, true)
    assert.match(editor.busyText.value, /整份重新生成/)
    const saved = { ...clone(original), id: 'saved-version' }
    resolve({ status: 'applied', plan: saved, changes: ['从新酒店出发'], notices: [], conflicts: [] })
    await pending
    assert.equal(calls, 1)
    assert.equal(editor.working.value, false)
    assert.equal(editor.opened.value, false)
    assert.equal(editor.preview.value, null)
    assert.equal(t.events.filter(([event]) => event === 'applied').length, 1)
    assert.equal(t.events.find(([event]) => event === 'applied')[1], saved)
    assert.equal(t.storage.has(draftKey(original.id)), false, 'successful save clears the old plan draft')
    assert.equal(t.obsoleteCalls(), 0, 'there is no separate preview/apply request')
    assert.equal(JSON.stringify(original), initialBytes)
  } finally { t.cleanup() }
})

await check('a conflict keeps the panel, hotel, ticket and draft; it never emits a saved plan', async () => {
  const t = harness(), { editor, adapter } = t
  try {
    await editor.open(); fill(editor)
    adapter.regenerateBookings = async () => ({ status: 'conflict', changes: [], notices: ['固定班次仍保持'], conflicts: ['两段已订交通时间互相重叠'] })
    await editor.regenerate()
    assert.equal(editor.working.value, false)
    assert.equal(editor.opened.value, true)
    assert.equal(editor.preview.value.status, 'conflict')
    assert.equal(editor.preview.value.preview_id, undefined)
    assert.equal(editor.hotels.value[0].name, hotel.name)
    assert.equal(editor.transports.value[0].arrival_place, '上海站')
    assert.equal(editor.confirmed.value, true)
    assert.equal(t.events.some(([event]) => event === 'applied'), false)
    assert.equal(t.obsoleteCalls(), 0)
    assert.equal(JSON.parse(t.storage.get(draftKey(original.id))).transports[0].number, train.number)
    assert.equal(JSON.stringify(original), initialBytes)
  } finally { t.cleanup() }
})

await check('closing aborts the request and ignores a late applied response', async () => {
  const t = harness(), { editor, adapter } = t
  try {
    await editor.open(); fill(editor)
    let resolve, signal
    adapter.regenerateBookings = (_id, _body, requestSignal) => {
      signal = requestSignal
      return new Promise(done => { resolve = done })
    }
    const pending = editor.regenerate()
    editor.close()
    assert.equal(signal.aborted, true)
    resolve({ status: 'applied', plan: { ...clone(original), id: 'late-save' }, changes: [], notices: [], conflicts: [] })
    await pending
    assert.equal(editor.opened.value, false)
    assert.equal(editor.preview.value, null)
    assert.equal(editor.working.value, false)
    assert.equal(t.events.some(([event]) => event === 'applied'), false, 'a dismissed response must not replace the viewed plan')
    assert(t.storage.has(draftKey(original.id)), 'aborting does not discard the local input draft')
  } finally { t.cleanup() }
})

await check('changing plan aborts pending regeneration and does not leak its response or draft into the new plan', async () => {
  const t = harness(), { editor, adapter } = t
  try {
    await editor.open(); fill(editor)
    let resolve, signal
    adapter.regenerateBookings = (_id, _body, requestSignal) => {
      signal = requestSignal
      return new Promise(done => { resolve = done })
    }
    const pending = editor.regenerate()
    t.props.plan = { ...clone(original), id: 'different-plan' }
    await vue.nextTick()
    assert.equal(signal.aborted, true)
    adapter.getBookingContext = async id => ({ ...clone(context), current_plan_id: id })
    await editor.open()
    assert.equal(editor.hotels.value.length, 0)
    assert.equal(editor.transports.value.length, 0)
    resolve({ status: 'applied', plan: { ...clone(original), id: 'late-old-plan' }, changes: [], notices: [], conflicts: [] })
    await pending
    assert.equal(editor.opened.value, true)
    assert.equal(editor.preview.value, null)
    assert.equal(t.events.some(([event]) => event === 'applied'), false)
    assert.equal(t.props.plan.id, 'different-plan')
    assert(t.storage.has(draftKey(original.id)))
    assert.equal(t.storage.has(draftKey('different-plan')), false)
  } finally { t.cleanup() }
})

await check('matching drafts restore after a reload without restoring confirmation; stale fingerprints discard them', async () => {
  const storage = new Map(), first = harness(storage)
  try { await first.editor.open(); fill(first.editor) } finally { first.cleanup() }
  const restored = harness(storage)
  try {
    await restored.editor.open()
    assert.equal(restored.editor.restored.value, true)
    assert.equal(restored.editor.confirmed.value, false, 'restored facts require another user acknowledgement')
    assert.equal(restored.editor.hotels.value[0].address, hotel.address)
    assert.equal(restored.editor.transports.value[0].arrival_place, '上海站')
  } finally { restored.cleanup() }
  const stale = harness(storage)
  try {
    const authoritative = { ...hotel, name: '服务器已有酒店' }
    stale.adapter.getBookingContext = async () => ({ ...clone(context), base_fingerprint: 'b'.repeat(64), confirmed_hotels: [authoritative] })
    await stale.editor.open()
    assert.equal(stale.editor.restored.value, false)
    assert.equal(stale.editor.hotels.value[0].name, authoritative.name)
    assert.equal(stale.editor.transports.value.length, 0)
    assert.equal(storage.has(draftKey(original.id)), false)
  } finally { stale.cleanup() }
})

await check('CAS errors retain the input draft and prohibit another regeneration until state is reread', async () => {
  const t = harness(), { editor, adapter } = t
  try {
    await editor.open(); fill(editor)
    let calls = 0
    adapter.regenerateBookings = async () => { calls++; throw new adapter.BookingApiError('行程状态已变化', 409) }
    await editor.regenerate()
    assert.equal(editor.stale.value, true)
    assert.match(editor.error.value, /状态已变化/)
    assert.equal(editor.opened.value, true)
    assert.equal(editor.hotels.value[0].name, hotel.name)
    await editor.regenerate()
    assert.equal(calls, 1)
    assert(t.storage.has(draftKey(original.id)))
    assert.equal(t.events.some(([event]) => event === 'applied'), false)
  } finally { t.cleanup() }
})

console.log(`${groups} booking regeneration regression groups passed; in-memory component checks only.`)
