// Render saved transport scope and incomplete budgets without model/API calls.
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { harness } from './check-appearance.mjs'

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
  plugins: [vue()], server: { middlewareMode: true }, appType: 'custom',
  optimizeDeps: { noDiscovery: true, include: [] },
})
try {
  const { default: Card } = await server.ssrLoadModule('/src/components/CityTransfers.vue')
  const { journeyCities, transferCoverage, transportRequestChanged } = await server.ssrLoadModule('/src/transportScope.ts')
  const old = { origin: '广州', destination: '拉萨市', destinations: [], intercity_mode: 'flight', include_origin_transport: false, return_to_origin: false }
  const trip = { ...old, include_origin_transport: true, return_to_origin: true }
  const transfers = [['广州市', '拉萨'], ['拉萨市', '广州']].map(([from_city, to_city]) => ({
    from_city, to_city, mode: 'flight', unit_cost: '800.25', basis: 'person', departure_date: '2026-10-21',
    arrival_date: '2026-10-21', departure_time: '09:00', arrival_time: '14:00', schedule_status: 'estimated', price_status: 'estimated',
  }))
  assert.deepEqual(journeyCities(trip), ['广州', '拉萨市', '广州'])
  assert(transportRequestChanged(trip, old))
  assert(!transportRequestChanged(trip, { ...trip, origin: '广州市', destination: '拉萨' }))
  assert(transferCoverage(trip, transfers).complete)
  assert(!transferCoverage(trip, [...transfers].reverse()).complete)
  assert.equal(transferCoverage(trip, transfers.slice(0, 1)).missing[0].to, '广州')
  for (const [label, request, legs, texts] of [
    ['old record without selected fares', old, [], ['不含出发交通', '不含返程交通', '预算不含这部分费用']],
    ['missing both selected legs', trip, [], ['缺少交通段', '广州 → 拉萨市', '拉萨市 → 广州', '当前预算尚不完整']],
    ['missing return fare', trip, transfers.slice(0, 1), ['缺少交通段', '拉萨市 → 广州']],
    ['complete estimated flight fares', trip, transfers, ['含出发交通', '含返程交通', '以下已列交通费用已计入', '未接入实时航班和机票查询']],
  ]) {
    const html = await renderToString(createSSRApp(Card, { request, transfers: legs }))
    for (const text of texts) assert(html.includes(text), `${label}: ${text}`)
    if (label.startsWith('complete')) assert(!html.includes('当前预算尚不完整'))
    console.log(`${label}: passed`)
  }
  // Repeated directed legs must not reuse a single fare twice.
  const repeated = { ...old, origin: '', destination: '广州→拉萨→广州→拉萨' }
  assert.equal(transferCoverage(repeated, transfers).missing.length, 1)
  console.log('scope comparison, aliases, ordered coverage and repeated legs: passed')
  for (const outcome of ['success', 'cancelled', 'dropped selection']) {
    const t = harness(), d = t.setup('App.vue')
    const stored = { id: 'old-lhasa', request: { ...d.form, ...old, days: 3 },
      itinerary: { days: [{ stops: [] }], transfers: [] }, routes: [], validation: { total_cents: 20000 } }
    t.api.api = async url => url === '/api/plans/old-lhasa' ? JSON.parse(JSON.stringify(stored))
      : url.endsWith('/validate') ? structuredClone(stored.validation) : []
    await d.openHistory('old-lhasa')
    assert.equal(d.plan.value?.id, 'old-lhasa'); assert.equal(d.form.origin, '广州')
    assert(!d.form.return_to_origin)
    d.form.return_to_origin = true
    // Payload must include outbound even if the checkbox change handler was not invoked.
    let submitted
    t.api.stream = async (url, body, signal, emit) => {
      assert.equal(url, '/api/plans/generate')
      submitted = JSON.parse(JSON.stringify(body))
      if (outcome === 'cancelled') throw new DOMException('cancelled', 'AbortError')
      emit('result', { ...stored, id: 'new-lhasa', request: outcome === 'success' ? submitted : stored.request,
        itinerary: { ...stored.itinerary, transfers } })
    }
    await d.run()
    assert(submitted.include_origin_transport && submitted.return_to_origin, outcome)
    if (outcome === 'success') {
      assert.equal(d.plan.value.id, 'new-lhasa'); assert.equal(d.error.value, '')
      assert.equal(d.plan.value.itinerary.transfers.length, 2)
    } else {
      assert.equal(d.plan.value.id, 'old-lhasa')
      assert(d.retainedPlanNotice.value.includes('预算尚未更新'))
      if (outcome === 'cancelled') assert(d.retainedPlanNotice.value.includes('已取消'))
      else assert(d.error.value.includes('未保留本次提交'))
    }
    assert(!d.busy.value); t.cleanup()
    console.log(`open history then regenerate: ${outcome}: passed`)
  }
} finally { await server.close() }
