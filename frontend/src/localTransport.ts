import type { Mode, RouteLeg, RouteOption } from './types'

export type LocalOption = RouteLeg | RouteOption
export const modeNames: Record<Mode, string> = { walking: '步行', transit: '公交 / 地铁', taxi: '打车', driving: '自驾', bicycling: '骑行' }

export function groupRange(option: LocalOption, people: number): [number, number] | null {
  if (!option.available) return null
  if (option.mode === 'walking' || option.stationary) return [0, 0]
  if (option.cost_estimate) {
    const multiplier = option.cost_estimate.basis === 'person' ? people : Math.ceil(people / 4)
    const low = Number(option.cost_estimate.low) * multiplier
    const high = Number(option.cost_estimate.high) * multiplier
    return Number.isFinite(low) && Number.isFinite(high) && low >= 0 && high >= low ? [low, high] : null
  }
  if (option.mode === 'transit' && option.fare_yuan != null) {
    const amount = Number(option.fare_yuan) * people
    return Number.isFinite(amount) && amount >= 0 ? [amount, amount] : null
  }
  return null
}

export function localRecommendation(options: LocalOption[], people: number): { mode: Mode | null; reason: string } {
  const known = new Map(options.filter(o => o.available && o.duration_s != null && o.duration_s >= 0 && o.distance_m != null && o.distance_m >= 0 && (o.polyline.length >= 2 || o.stationary)).map(o => [o.mode, o]))
  const walk = known.get('walking'), transit = known.get('transit'), taxi = known.get('taxi')
  if (walk && walk.distance_m! <= 1500 && walk.duration_s! <= 1200) return { mode: 'walking', reason: '短途步行：道路不超过1.5公里且20分钟，免交通付费。' }
  if (transit && taxi) {
    const publicCost = groupRange(transit, people), carCost = groupRange(taxi, people)
    const lines = (transit.instructions || []).filter(name => name.trim())
    const changes = lines.length ? Math.max(0, lines.length - 1) : null
    const times = `打车约${Math.ceil(taxi.duration_s! / 60)}分钟，公交／地铁约${Math.ceil(transit.duration_s! / 60)}分钟`
    const connection = changes == null ? '，公交换乘细节待核实' : `，公交需换乘${changes}次`
    if (publicCost && carCost && carCost[1] <= publicCost[1] && taxi.duration_s! <= transit.duration_s! + 300) return { mode: 'taxi', reason: `${times}；全员费用参考不高于公交，约少花${(publicCost[1] - carCost[1]).toFixed(2)}元${connection}；默认综合全员费用、时间和换乘取舍，可改选。` }
    if (changes != null && changes >= 2 && publicCost && carCost && carCost[1] - publicCost[1] <= 10 * people && taxi.duration_s! <= transit.duration_s!) return { mode: 'taxi', reason: `${times}，公交需换乘${changes}次；全员增加约${(carCost[1] - publicCost[1]).toFixed(2)}元，地图路耗不更长；默认在每人多付不超过10元时优先减少多次换乘，可改选。` }
    if (transit.duration_s! <= taxi.duration_s! + 900 && publicCost && carCost && publicCost[1] < carCost[1]) return { mode: 'transit', reason: `${times}；全员费用更低，约少花${(carCost[1] - publicCost[1]).toFixed(2)}元${connection}；默认在比打车慢不超过15分钟时优先控制全员费用，可改选。` }
    if (taxi.duration_s! + 900 <= transit.duration_s!) {
      const savedMinutes = (transit.duration_s! - taxi.duration_s!) / 60
      if (publicCost && carCost && carCost[1] - publicCost[1] > savedMinutes * people) return { mode: 'transit', reason: `${times}，比打车约多花${Math.ceil(savedMinutes)}分钟，但全员约少花${(carCost[1] - publicCost[1]).toFixed(2)}元${connection}；按默认每人每省1分钟最多增加1元的产品规则取舍，可自行改选。` }
      if (!publicCost || !carCost) return { mode: 'taxi', reason: `${times}，约节省${Math.ceil(savedMinutes)}分钟${connection}；费用未齐，仅耗时参考需确认，不能据此判断全员花费更合适。` }
      return { mode: 'taxi', reason: `${times}，约节省${Math.ceil(savedMinutes)}分钟，全员多约${(carCost[1] - publicCost[1]).toFixed(2)}元${connection}；按默认每人每省1分钟最多增加1元的产品规则综合取舍，可改选。` }
    }
  }
  if (taxi && !transit && (!walk || walk.duration_s! - taxi.duration_s! >= 900)) return { mode: 'taxi', reason: '尚无可用公交资料，步行较远或耗时更多；不代表公交实际停运。' }
  const order: Mode[] = ['walking', 'transit', 'taxi']
  const primary = order.map(mode => known.get(mode)).filter((o): o is LocalOption => o != null).sort((a, b) => a.duration_s! - b.duration_s! || order.indexOf(a.mode) - order.indexOf(b.mode))
  if (primary[0]) return { mode: primary[0].mode, reason: '常用方式中已查地图路耗最短；价格依据不足时不声称最便宜。' }
  return { mode: null, reason: '常用方式资料不足，暂不推荐；可查看更多方式或更新路线。' }
}
