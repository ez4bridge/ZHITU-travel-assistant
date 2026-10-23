// Exercise the merged scenario routing without calling model, maps or a database.
import assert from 'node:assert/strict'
import { harness } from './check-appearance.mjs'

const t = harness(), d = t.setup('App.vue')
try {
  const stop = { id: 'a', name: '已核实地点', city: '上海', kind: 'sight', start: '10:00', end: '11:00' }
  const original = { id: 'travel', request: { ...d.form, days: 1, study: null }, itinerary: { days: [{ date: '2026-10-23', stops: [stop], expenses: [] }], transfers: [], tips: [] }, routes: [] }
  d.plan.value = original
  d.plan.value = { ...original, itinerary: { ...original.itinerary, days: [{ ...original.itinerary.days[0], stops: [stop, { ...stop, id: 'b' }] }] },
    routes: [{ day_index: 0, from_id: 'a', to_id: 'stale-destination', duration_s: 99 }, { day_index: 0, from_id: 'a', to_id: 'b', duration_s: 123 }] }
  assert.equal(d.routeAfter('a').to_id, 'b')
  d.plan.value.routes = [d.plan.value.routes[0]]
  assert.equal(d.routeAfter('a'), undefined, 'a stale route to another destination must not be attached to the next activity')
  d.plan.value = original
  console.log('route cards match both endpoints of the backend adjacent activity pair: passed')
  let instruction = '', routeMode = '', calls = 0
  d.adjustmentPanel.value = {
    openWithInstruction(value) { instruction = value },
    async openWithRoute(route, mode) { assert.equal(route.from_id, 'a'); routeMode = mode },
  }
  t.api.stream = async () => { throw new Error('普通旅行不得绕过约束预览直接改写') }
  d.reviseProblems()
  assert.match(instruction, /真实路线/)
  await d.chooseRoute({ from_id: 'a' }, 'transit')
  assert.equal(routeMode, 'transit')
  assert.equal(d.plan.value.id, 'travel')
  console.log('travel modifications and local route changes enter one preview workspace: passed')

  d.form.intercity_modes = ['train', 'flight']
  d.form.confirmed_hotels = [{ city: '上海', name: '酒店甲', check_in: '2026-10-23', check_out: '2026-10-24' }]
  d.switchMode(true)
  assert(d.studyMode.value)
  assert.equal(d.form.study.max_stops, 3)
  assert.equal(d.plan.value, null)
  d.plan.value = { ...original, id: 'study', request: { ...d.form, study: { ...d.form.study } }, study_context: { sources: [{ id: 'source' }] } }
  t.api.stream = async (url, payload) => {
    calls++; assert.equal(url, '/api/plans/study/revise')
    assert(payload.trip.study); assert.equal(payload.trip.include_origin_transport, false)
    assert.equal(payload.trip.days, 1)
  }
  t.api.api = async () => []
  d.revision.value = '保留带出处的讲解，缩短至两小时'
  await d.run(true)
  assert.equal(calls, 1)
  assert.equal(d.plan.value.study_context.sources[0].id, 'source')
  d.switchMode(false)
  assert.equal(d.plan.value.id, 'travel')
  assert.deepEqual(Array.from(d.form.intercity_modes), ['train', 'flight'])
  assert.equal(d.form.confirmed_hotels[0].name, '酒店甲')
  console.log('study keeps its generator and evidence; switching modes restores independent plans and hotels: passed')

  d.constraintBusy.value = true
  d.switchMode(true)
  assert.equal(d.studyMode.value, false)
  await d.run()
  assert.equal(calls, 1)
  d.constraintBusy.value = false
  d.bookingBusy.value = true
  await d.chooseRoute({ from_id: 'a' }, 'taxi')
  assert.equal(routeMode, 'transit')
  d.bookingBusy.value = false
  console.log('booking and constraint operations block competing mode changes and writes: passed')
} finally { t.cleanup() }
