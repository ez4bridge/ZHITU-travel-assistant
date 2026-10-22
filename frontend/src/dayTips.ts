import type { Plan } from './types'

function dayNumber(value: string): number {
  if (/^\d+$/.test(value)) return Number(value)
  const digits: Record<string, number> = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 }
  return digits[value] || 0
}

const key = (text: string) => text.replace(/[\s·•（）()]/g, '').toLowerCase()

/** Attribute old flat reminders conservatively; uncertain text remains visible. */
export function groupTravelTips(plan: Plan): { days: string[][]; general: string[] } {
  const days = plan.itinerary.days.map(day => [...new Set(day.tips || [])])
  const general: string[] = []
  const already = new Set(days.flat())
  const placeDays = (text: string) => plan.itinerary.days.flatMap((day, index) => day.stops.some(stop =>
    [stop.name, stop.place_name, stop.matched_name, stop.area_name].some(alias => alias && key(alias).length >= 3 && key(text).includes(key(alias)))) ? [index] : [])
  for (const original of new Set(plan.itinerary.tips || [])) {
    const text = original.trim()
    if (!text || already.has(text)) continue
    let indexes: number[] = []
    const explicit = [...text.matchAll(/第([一二两三四五六七八九十\d]+)天/g)]
    if (explicit.length) {
      indexes = explicit.map(match => dayNumber(match[1]!) - 1).filter(index => index >= 0 && index < days.length)
    } else {
      const dates = [...text.matchAll(/(?:(20\d{2})[-/])?(\d{1,2})[-/月](\d{1,2})(?:日|号)?/g)]
      if (dates.length) {
        indexes = plan.itinerary.days.flatMap((day, index) => dates.some(match => {
          const [year, month, date] = day.date.split('-').map(Number)
          return (!match[1] || Number(match[1]) === year) && Number(match[2]) === month && Number(match[3]) === date
        }) ? [index] : [])
        // Old reminder prose can mention a date different from the actual visit.
        // Keep the warning on the visit day too, without rewriting its text/date.
        indexes.push(...placeDays(text))
      }
      if (!dates.length) {
        if (/全程|全员|总预算|预算\s*\d|跨城|机场|车站|进站|出站/.test(text)) { general.push(text); continue }
        const needle = key(text)
        indexes = placeDays(text)
        if (!indexes.length) {
          const cities = new Set(plan.itinerary.days.flatMap(day => day.stops.map(stop => stop.city?.replace(/市$/, '')).filter(Boolean)))
          const mentioned = [...cities].filter(city => needle.includes(key(city)))
          // A comparison across cities is a whole-trip reminder, not an arbitrary
          // assignment to the first city mentioned in the sentence.
          if (mentioned.length === 1) indexes = plan.itinerary.days.flatMap((day, index) =>
            day.stops.some(stop => stop.city?.replace(/市$/, '') === mentioned[0]) ? [index] : [])
        }
      }
    }
    if (!indexes.length) general.push(text)
    else for (const index of new Set(indexes)) if (!days[index]!.includes(text)) days[index]!.push(text)
  }
  return { days, general }
}
