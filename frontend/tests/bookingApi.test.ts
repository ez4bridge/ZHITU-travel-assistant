import assert from 'node:assert/strict'
import test from 'node:test'
import { BookingApiError, getBookingContext } from '../src/bookingApi.ts'

const baseContext = {
  base_fingerprint: 'a'.repeat(64), expected_routing_revision: 2, expected_state_revision: 3,
  current_plan_id: 'plan/1',
}

await test('Decimal JSON strings become editable numeric costs without turning zero into unknown', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async (input, init) => {
      assert.equal(input, '/api/plans/plan%2F1/booking-context')
      assert.equal(init?.method, 'GET')
      return new Response(JSON.stringify({ ...baseContext,
        confirmed_hotels: [
          { city: '北京市', name: '甲酒店', check_in: '2026-10-24', check_out: '2026-10-26', total_cost: '699.99' },
          { city: '天津市', name: '乙酒店', check_in: '2026-10-26', check_out: '2026-10-27', total_cost: '0.00' },
        ],
        booked_transports: [
          { from_city: '北京', to_city: '天津', mode: 'train', departure_date: '2026-10-26', departure_time: '09:00', arrival_date: '2026-10-26', arrival_time: '09:30', total_cost: '123.45' },
          { from_city: '天津', to_city: '北京', mode: 'train', departure_date: '2026-10-27', departure_time: '09:00', arrival_date: '2026-10-27', arrival_time: '09:30', total_cost: null },
        ],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    const result = await getBookingContext('plan/1', new AbortController().signal)
    assert.deepEqual(result.confirmed_hotels.map(item => item.total_cost), [699.99, 0])
    assert.deepEqual(result.booked_transports.map(item => item.total_cost), [123.45, null])
    assert.equal(result.expected_state_revision, 3)
  } finally { globalThis.fetch = originalFetch }
})

await test('invalid stored money fails visibly instead of silently discarding a known booking amount', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ ...baseContext,
      confirmed_hotels: [{ city: '北京', name: '甲酒店', check_in: '2026-10-24', check_out: '2026-10-26', total_cost: 'NaN' }],
      booked_transports: [],
    }), { status: 200 })
    await assert.rejects(() => getBookingContext('plan/1', new AbortController().signal), (error: unknown) =>
      error instanceof BookingApiError && error.status === 422 && error.message.includes('费用格式异常'))
  } finally { globalThis.fetch = originalFetch }
})

await test('stale booking context keeps the conflict status so the editor offers a fresh read', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '已确认安排已变化' }), { status: 409 })
    await assert.rejects(() => getBookingContext('plan/1', new AbortController().signal), (error: unknown) =>
      error instanceof BookingApiError && error.status === 409 && error.message === '已确认安排已变化')
  } finally { globalThis.fetch = originalFetch }
})
