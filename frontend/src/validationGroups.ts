import type { DayPlan, Issue, ValidationReport } from './types'

export type IssueKind = 'errors' | 'warnings' | 'unknowns'
export interface IssueRow {
  key: string
  message: string
  count: number
  places: string[]
}
export interface ValidationGroup {
  key: string
  dayIndex: number | null
  title: string
  date: string
  cities: string
  counts: Record<IssueKind, number>
  rows: Record<IssueKind, IssueRow[]>
}

export function validationGroups(report: ValidationReport, days: DayPlan[]): ValidationGroup[] {
  const groups = new Map<number | null, ValidationGroup>()
  const stops = new Map(days.flatMap(day => day.stops.map(stop => [stop.id, stop.name] as const)))
  const kinds: IssueKind[] = ['errors', 'warnings', 'unknowns']
  for (const kind of kinds) {
    for (const issue of report[kind]) {
      const index = dayIndex(issue)
      let group = groups.get(index)
      if (!group) {
        const day = index === null ? undefined : days[index]
        group = {
          key: index === null ? 'whole-trip' : `day-${index}`,
          dayIndex: index,
          title: index === null ? '整程提醒' : `第 ${index + 1} 天`,
          date: day?.date || '',
          cities: [...new Set(day?.stops.map(stop => stop.city).filter(Boolean) || [])].join(' · '),
          counts: { errors: 0, warnings: 0, unknowns: 0 },
          rows: { errors: [], warnings: [], unknowns: [] },
        }
        groups.set(index, group)
      }
      group.counts[kind]++
      // Merge repeated wording within this day and severity, retaining all affected places.
      const key = JSON.stringify([issue.code, issue.message.trim()])
      let row = group.rows[kind].find(item => item.key === key)
      if (!row) {
        row = { key, message: issue.message, count: 0, places: [] }
        group.rows[kind].push(row)
      }
      row.count++
      const place = issue.stop_id ? stops.get(issue.stop_id) : undefined
      if (place && !row.places.includes(place)) row.places.push(place)
    }
  }
  return [...groups.values()].sort((a, b) => (a.dayIndex ?? Infinity) - (b.dayIndex ?? Infinity))
}

function dayIndex(issue: Issue): number | null {
  return typeof issue.day_index === 'number' && Number.isInteger(issue.day_index) && issue.day_index >= 0
    ? issue.day_index : null
}
