import type { CityTransfer, Plan, Stop } from './types'

interface TimelineBase { key: string; startMinute: number; endMinute: number; conflicts?: string[] }
export interface StopTimelineEvent extends TimelineBase { kind: 'stop'; stop: Stop; stopIndex: number }
export interface TransferTimelineEvent extends TimelineBase {
  kind: 'transfer'; transfer: CityTransfer; transferIndex: number
  continuedFromPrevious: boolean; continuesNextDay: boolean
}
export type DayTimelineEvent = StopTimelineEvent | TransferTimelineEvent

function dayStamp(date: string) { return Date.parse(`${date}T00:00:00Z`) }
function minute(time: string) {
  const [hour, value] = time.split(':').map(Number)
  return Number.isFinite(hour) && Number.isFinite(value) ? hour! * 60 + value! : NaN
}

const cityKey = (city: string) => (city || '').trim().replace(/市$/, '')
function stationAnchor(stops: StopTimelineEvent[], transfer: CityTransfer, role: 'departure' | 'arrival', minute: number) {
  const city = role === 'departure' ? transfer.from_city : transfer.to_city
  const station = (role === 'departure' ? transfer.departure_station : transfer.arrival_station) || ''
  const anchors = stops.filter(event => event.stop.booking_anchor === `transport_${role}` && cityKey(event.stop.city) === cityKey(city))
  const named = station ? anchors.filter(event => `${event.stop.name} ${event.stop.place_name || ''} ${event.stop.matched_name || ''}`.includes(station)) : []
  return (named.length ? named : anchors).reduce<StopTimelineEvent | undefined>((nearest, event) => {
    const time = role === 'departure' ? event.endMinute : event.startMinute
    const previous = nearest && (role === 'departure' ? nearest.endMinute : nearest.startMinute)
    return !nearest || Math.abs(time - minute) < Math.abs(previous! - minute) ? event : nearest
  }, undefined)
}

function label(event: DayTimelineEvent) {
  return event.kind === 'stop' ? event.stop.name : `${event.transfer.from_city}至${event.transfer.to_city}跨城交通`
}

/** The backend stop array is the itinerary and route order. Time conflicts must not reorder it. */
export function buildDayTimeline(plan: Pick<Plan, 'itinerary'>, dayIndex: number): DayTimelineEvent[] {
  const day = plan.itinerary.days[dayIndex]
  if (!day) return []
  const stops: StopTimelineEvent[] = day.stops.map((stop, stopIndex) => ({ kind: 'stop', key: `stop:${stop.id}`,
    stop, stopIndex, startMinute: minute(stop.start), endMinute: minute(stop.end) }))
  const insertions = new Map<number, TransferTimelineEvent[]>()
  const start = dayStamp(day.date), end = start + 86400000
  if (!Number.isFinite(start)) return stops
  for (const [transferIndex, transfer] of plan.itinerary.transfers.entries()) {
    const departure = dayStamp(transfer.departure_date) + minute(transfer.departure_time) * 60000
    const arrival = dayStamp(transfer.arrival_date) + minute(transfer.arrival_time) * 60000
    if (!Number.isFinite(departure) || !Number.isFinite(arrival) || arrival <= departure || departure >= end || arrival < start) continue
    const event: TransferTimelineEvent = { kind: 'transfer', key: `transfer:${transferIndex}:${day.date}`, transfer, transferIndex,
      startMinute: Math.max(0, (departure - start) / 60000), endMinute: Math.min(1440, (arrival - start) / 60000),
      continuedFromPrevious: departure < start, continuesNextDay: transfer.arrival_date > day.date }
    const departureAnchor = transfer.departure_date === day.date ? stationAnchor(stops, transfer, 'departure', event.startMinute) : undefined
    const arrivalAnchor = transfer.arrival_date === day.date ? stationAnchor(stops, transfer, 'arrival', event.endMinute) : undefined
    let position = departureAnchor ? departureAnchor.stopIndex + 1 : arrivalAnchor ? arrivalAnchor.stopIndex
      : stops.findIndex(stop => Number.isFinite(stop.startMinute) && stop.startMinute >= event.startMinute)
    if (position < 0) position = stops.length
    if (departureAnchor && arrivalAnchor && departureAnchor.stopIndex >= arrivalAnchor.stopIndex) {
      event.conflicts = ['出发与到达接驳节点的顺序冲突；保留原有活动顺序，请重新规划核对。']
    }
    const bucket = insertions.get(position) || []
    bucket.push(event); insertions.set(position, bucket)
  }
  const events: DayTimelineEvent[] = []
  for (let position = 0; position <= stops.length; position++) {
    events.push(...(insertions.get(position) || []))
    if (stops[position]) events.push(stops[position]!)
  }
  for (const [index, event] of events.entries()) {
    const conflicts = [...(event.conflicts || [])]
    if (!Number.isFinite(event.startMinute) || !Number.isFinite(event.endMinute) || event.endMinute < event.startMinute || (event.kind === 'stop' && event.endMinute === event.startMinute)) {
      conflicts.push('该节点的开始或结束时间异常，需重新核对；页面未自动修改时间。')
    }
    const preceding = events.slice(0, index)
    const overlap = preceding.filter(previous => Number.isFinite(previous.startMinute) && Number.isFinite(previous.endMinute)
      && event.startMinute < previous.endMinute && event.endMinute > previous.startMinute)
    if (overlap.length) conflicts.push(`与前序安排“${overlap.map(label).join('”“')}”时间重叠；保留原有顺序，请调整后再执行。`)
    else if (preceding.some(previous => Number.isFinite(previous.startMinute) && event.startMinute < previous.startMinute)) {
      conflicts.push('开始时间早于前序安排，时序存在冲突；保留原有顺序，请重新核对。')
    }
    if (conflicts.length) event.conflicts = conflicts
  }
  return events
}
