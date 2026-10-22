import assert from 'node:assert/strict'
import test from 'node:test'
import { getPreparation } from '../src/preparationApi.ts'
import { preparationState, preparationTitles, safePreparationLink } from '../src/preparationPresentation.ts'
import type { PreparationSummary } from '../src/preparationTypes.ts'

const snapshot: PreparationSummary = { state: 'ready', title: '服务端说明', summary: '只读核对', missing_count: 0,
  hotels: [], transports: [], blocking: [], unknowns: ['票价仍需核实'] }

await test('missing arrangements and hard errors cannot be presented as a confirmed itinerary', () => {
  assert.equal(preparationState({ ...snapshot, missing_count: 2 }), 'preview')
  assert.match(preparationTitles[preparationState({ ...snapshot, missing_count: 2 })], /行程预览/)
  assert.equal(preparationState({ ...snapshot, blocking: ['路线无法核实'] }), 'needs_review')
  assert.match(preparationTitles.needs_review, /待核实/)
  assert.equal(preparationState(snapshot), 'ready')
  assert.match(preparationTitles.ready, /价格、开放仍需复核/)
})

await test('metadata requests are encoded, read-only and cancellable, without touching the saved plan', async () => {
  const original = globalThis.fetch
  try {
    const signal = new AbortController().signal
    globalThis.fetch = async (url, init) => {
      assert.equal(url, '/api/plans/plan%2Fchild/preparation')
      assert.equal(init?.method, 'GET')
      assert.equal(init?.signal, signal)
      assert.equal(init?.body, undefined)
      return new Response(JSON.stringify(snapshot), { status: 200 })
    }
    assert.deepEqual(await getPreparation('plan/child', signal), snapshot)
  } finally { globalThis.fetch = original }
})

await test('failed or malformed metadata never defaults to ready', async () => {
  const original = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '研学不使用普通旅行准备核对' }), { status: 422 })
    await assert.rejects(() => getPreparation('study', new AbortController().signal), /研学/)
    globalThis.fetch = async () => new Response(JSON.stringify({ state: 'ready', missing_count: 0 }), { status: 200 })
    await assert.rejects(() => getPreparation('incomplete', new AbortController().signal), /不完整/)
    globalThis.fetch = async () => new Response(JSON.stringify({ ...snapshot, state: 'confirmed' }), { status: 200 })
    await assert.rejects(() => getPreparation('unexpected', new AbortController().signal), /不完整/)
  } finally { globalThis.fetch = original }
})

await test('external suggestions only allow normal web links', () => {
  assert.equal(safePreparationLink('https://www.12306.cn/index/'), 'https://www.12306.cn/index/')
  assert.equal(safePreparationLink('javascript:alert(1)'), '')
  assert.equal(safePreparationLink('file:///D:/secrets'), '')
  assert.equal(safePreparationLink(''), '')
})
