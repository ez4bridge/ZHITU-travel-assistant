import type { Plan } from './types'

export function planRecovery(plan: Plan) {
  const stops = new Map(plan.itinerary.days.flatMap(day => day.stops).map(stop => [stop.id, stop]))
  const lookup = [], planning = []
  for (const issue of plan.validation.errors) {
    const stop = issue.stop_id ? stops.get(issue.stop_id) : undefined
    const leg = issue.code === 'missing_route' ? plan.routes.find(route => route.day_index === issue.day_index && route.to_id === issue.stop_id) : undefined
    const unspecified = stop?.location_behavior === 'unlocated' || (leg && [leg.from_id, leg.to_id].some(id => stops.get(id)?.location_behavior === 'unlocated'))
    if (['missing_route', 'missing_location'].includes(issue.code) && !unspecified) lookup.push(issue)
    else planning.push(issue)
  }
  const configuration = plan.routes.some(route => ['map_auth', 'map_quota', 'map_permission', 'map_ip_limit'].includes(route.failure_code || ''))
  return { lookupCount: lookup.length, planningCount: planning.length, configuration }
}
