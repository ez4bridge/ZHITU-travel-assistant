import assert from 'node:assert/strict'
import { buildDayTimeline } from '../src/dayTimeline.ts'
import type { Plan } from '../src/types.ts'

const plan = { itinerary: {
  days: [{ date: '2026-10-23', stops: [{ id: 'a', start: '09:00', end: '10:00' }, { id: 'b', start: '14:00', end: '15:00' }] },
    { date: '2026-10-24', stops: [{ id: 'c', start: '10:00', end: '11:00' }] },
    { date: '2026-10-25', stops: [] }],
  transfers: [{ from_city: '北京', to_city: '天津', departure_date: '2026-10-23', departure_time: '11:00', arrival_date: '2026-10-23', arrival_time: '12:00' },
    { from_city: '天津', to_city: '上海', departure_date: '2026-10-23', departure_time: '22:00', arrival_date: '2026-10-24', arrival_time: '08:00' }],
} } as unknown as Plan
const first = buildDayTimeline(plan, 0)
assert.deepEqual(first.map(event => event.kind), ['stop', 'transfer', 'stop', 'transfer'])
assert.deepEqual(first.map(event => event.startMinute), [540, 660, 840, 1320])
assert.equal(first[3]!.kind === 'transfer' && first[3]!.continuesNextDay, true)
const second = buildDayTimeline(plan, 1)
assert.equal(second[0]!.kind, 'transfer')
assert.equal(second[0]!.startMinute, 0)
assert.equal(second[0]!.endMinute, 480)
assert.equal(second[0]!.kind === 'transfer' && second[0]!.continuedFromPrevious, true)
assert.equal(second.filter(event => event.kind === 'transfer').length, 1)
assert.deepEqual(buildDayTimeline(plan, 2), [])
assert.deepEqual(buildDayTimeline(plan, 20), [])
plan.itinerary.transfers[1]!.arrival_time = '00:00'
const midnight = buildDayTimeline(plan, 1)[0]!
assert.equal(midnight.kind, 'transfer', 'midnight arrival stays visible on its arrival date')
assert.equal(midnight.endMinute, 0, 'midnight arrival is a moment, not an extra whole day on the train')
plan.itinerary.days[0]!.stops = [
  { id: 'hotel', start: '09:00', end: '09:05' }, { id: 'departure-station', start: '10:00', end: '10:05' },
  { id: 'arrival-station', start: '12:30', end: '12:35' }, { id: 'afternoon', start: '14:00', end: '15:00' },
] as Plan['itinerary']['days'][number]['stops']
assert.deepEqual(buildDayTimeline(plan, 0).slice(0, 5).map(event => event.kind === 'stop' ? event.stop.id : 'ride'),
  ['hotel', 'departure-station', 'ride', 'arrival-station', 'afternoon'])
const conflictPlan = { itinerary: { days: [{ date: '2026-10-23', stops: [
  { id: 'first', name: '晚餐', start: '20:00', end: '21:00' },
  { id: 'bund', name: '外滩', start: '21:51', end: '23:01' },
  { id: 'road', name: '南京路', start: '21:40', end: '22:30' },
] }], transfers: [] } } as unknown as Plan
const saved = JSON.stringify(conflictPlan)
const conflictTimeline = buildDayTimeline(conflictPlan, 0)
assert.deepEqual(conflictTimeline.filter(event => event.kind === 'stop').map(event => [event.stop.id, event.stopIndex]),
  [['first', 0], ['bund', 1], ['road', 2]], 'conflicting start times must not reorder backend nodes or change their numbers')
assert.match(conflictTimeline[2]!.conflicts!.join(' '), /外滩.*时间重叠/, 'the original conflicting order must be visible, not silently repaired')
assert.equal(JSON.stringify(conflictPlan), saved, 'the frontend must never rewrite saved times, itinerary order or route ownership')

const nested = { itinerary: { days: [{ date: '2026-10-23', stops: [
  { id: 'long', name: '长时活动', start: '09:00', end: '14:00' },
  { id: 'short', name: '短活动', start: '10:00', end: '11:00' },
  { id: 'last', name: '后续活动', start: '12:00', end: '13:00' },
] }], transfers: [] } } as unknown as Plan
assert.match(buildDayTimeline(nested, 0)[2]!.conflicts!.join(' '), /长时活动/, 'overlap with an earlier nonadjacent activity must also be shown')

const stationConflict = { itinerary: { days: [{ date: '2026-10-23', stops: [
  { id: 'station', city: '北京', name: '北京站', booking_anchor: 'transport_departure', start: '11:10', end: '11:15' },
  { id: 'arrival', city: '天津', name: '天津站', booking_anchor: 'transport_arrival', start: '12:30', end: '12:35' },
] }], transfers: [{ from_city: '北京', to_city: '天津', departure_station: '北京站', arrival_station: '天津站',
  departure_date: '2026-10-23', departure_time: '11:00', arrival_date: '2026-10-23', arrival_time: '12:00' }] } } as unknown as Plan
const stationTimeline = buildDayTimeline(stationConflict, 0)
assert.deepEqual(stationTimeline.map(event => event.kind === 'stop' ? event.stop.id : 'ride'), ['station', 'ride', 'arrival'],
  'a departure anchor remains before its transfer even when the saved station time is wrong')
assert.match(stationTimeline[1]!.conflicts!.join(' '), /时间重叠/, 'late arrival at a departure station remains an explicit conflict')
console.log('dayTimeline: backend order, visible overlaps, route numbering, station anchors and overnight continuation passed')
