import assert from 'node:assert/strict'
import { localRecommendation } from '../src/localTransport.ts'
import type { Mode, RouteOption } from '../src/types.ts'

function route(mode: Mode, duration: number, price: number, instructions: string[] = []): RouteOption {
  return { mode, available: true, duration_s: duration, distance_m: 5000,
    polyline: [[116.1, 39.1], [116.2, 39.2]], instructions,
    cost_estimate: { low: price, high: price, basis: mode === 'taxi' ? 'vehicle' : 'person',
      method: 'test', assumptions: 'synthetic', source_url: '' } } as RouteOption
}

const publicRoute = route('transit', 1800, 5, ['地铁1号线', '公交2路', '公交3路'])
let result = localRecommendation([publicRoute, route('taxi', 1400, 25)], 2)
assert.equal(result.mode, 'taxi')
assert(result.reason.includes('换乘2次'))
result = localRecommendation([publicRoute, route('taxi', 1400, 100)], 2)
assert.equal(result.mode, 'transit')
result = localRecommendation([route('transit', 2700, 5), route('taxi', 1500, 200)], 2)
assert.equal(result.mode, 'transit')
assert(result.reason.includes('最多增加1元'))
result = localRecommendation([route('transit', 1200, 10), route('taxi', 1250, 35)], 4)
assert.equal(result.mode, 'taxi')
result = localRecommendation([route('transit', 2700, 5), route('taxi', 1200, 30)], 1)
assert.equal(result.mode, 'taxi')
assert(result.reason.includes('打车约20分钟') && result.reason.includes('节省25分钟') && result.reason.includes('全员多约25.00元'))
const unknownFare = { ...route('transit', 2700, 5), cost_estimate: null }
result = localRecommendation([unknownFare, route('taxi', 1200, 30)], 1)
assert(result.reason.includes('费用未齐，仅耗时参考需确认'))
result = localRecommendation([route('transit', 1800, 5), { ...route('taxi', 300, 25), polyline: [] }], 2)
assert.equal(result.mode, 'transit')
console.log('localTransport: group budget, convenience, time and incomplete geometry checks passed')
