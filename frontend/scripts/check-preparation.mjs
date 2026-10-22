// Test metadata cancellation and stale response handling in the real component setup.
// Uses a stubbed read-only adapter; no browser, key, model, maps or database.
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { harness } from './check-appearance.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const t = harness()
const adapter = t.load(path.join(root, 'src/preparationApi.ts'))
const props = { plan: { id: 'one', routing_revision: 0, validation: { errors: [] } }, disabled: false }
const component = t.setup('components/PreparationCard.vue', props)
const ready = { state: 'ready', title: '已核对', summary: '检查通过', missing_count: 0, hotels: [], transports: [], blocking: [], unknowns: [] }
try {
  let firstResolve, secondResolve
  const requests = []
  adapter.getPreparation = (id, signal) => {
    requests.push({ id, signal })
    return new Promise(resolve => { if (id === 'one') firstResolve = resolve; else secondResolve = resolve })
  }
  const first = component.refresh()
  assert.equal(component.loading.value, true)
  // Simulate the new prop while an old metadata call is still running.
  props.plan.id = 'two'
  const second = component.refresh()
  assert.equal(requests[0].signal.aborted, true)
  assert.equal(component.summary.value, null)
  firstResolve(ready); await first
  assert.equal(component.summary.value, null, 'an old ready response must not confirm the new plan')
  secondResolve({ ...ready, state: 'needs_review', blocking: ['到站接驳尚未核实'] }); await second
  assert.equal(component.state.value, 'needs_review')
  assert.match(component.title.value, /待核实/)
  console.log('new plan aborts previous metadata; stale ready responses are ignored: passed')

  adapter.getPreparation = async () => { throw new Error('读取失败') }
  await component.refresh()
  assert.equal(component.summary.value, null)
  assert.equal(component.state.value, null)
  assert.match(component.error.value, /读取失败/)
  assert.doesNotMatch(component.title.value, /已核对行程/)
  console.log('metadata failure clears old readiness and remains visibly unverified: passed')
} finally { t.cleanup() }
