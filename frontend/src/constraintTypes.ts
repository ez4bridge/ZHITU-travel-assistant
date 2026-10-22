import type { Mode, Plan } from './types'

export type ConstraintMode = 'requirements' | 'during'
export type ActivityStatus = 'planned' | 'completed' | 'missed' | 'cancelled'
export type ActivityPriority = 'must' | 'want' | 'optional'
export interface ConstraintEngine {
  available?: boolean
  name: string
  version: string
  diagnostic?: string
}
export interface ConstraintDay {
  day_index: number
  date: string
  cities: string[]
  stop_count: number
  supported: boolean
  reason: string
  start_min: number
  end_min: number
  city_scopes?: { city: string; supported: boolean; reason: string; stop_count: number; start_min: number; end_min: number }[]
}
export interface ConstraintDayWindow { day_index: number; start_min: number; end_min: number }
export interface ConstraintRouteChoice { day_index: number; from_id: string; to_id: string; mode: Mode }
export interface ConstraintRouteOption {
  mode: Mode
  available: boolean
  duration_s: number | null
  distance_m: number | null
  cost_cents: number | null
  cost_state: 'estimated' | 'unknown'
  recommended: boolean
  note: string
}
export interface ConstraintRoute {
  day_index: number
  from_id: string
  to_id: string
  from_name: string
  to_name: string
  mode: Mode
  recommended_mode: Mode | null
  recommendation_reason: string
  can_query?: boolean
  options: ConstraintRouteOption[]
}
export interface ConstraintStop {
  stop_id: string
  day_index: number
  date: string
  name: string
  city: string
  kind: 'sight' | 'meal' | 'hotel' | 'break'
  start: string
  end: string
  cost_cents: number
  status: ActivityStatus
  priority: ActivityPriority
  locked: boolean
  removable_cost: boolean
  removable_cost_allowed: boolean
  retain_cost?: boolean
  booking_anchor?: string
}
export interface ConstraintContext {
  schema_version: string
  base_plan_id: string
  base_fingerprint: string
  routing_revision: number
  state_revision: number
  current_plan_id: string
  engine: ConstraintEngine
  budget_cents: number
  days: ConstraintDay[]
  stops: ConstraintStop[]
  routes: ConstraintRoute[]
  notices: string[]
}
export interface ConstraintActivity {
  stop_id: string
  status: ActivityStatus
  priority: ActivityPriority
  locked: boolean
  removable_cost: boolean
  retain_cost?: boolean
}
export interface ConstraintPreviewRequest {
  schema_version: '1.0'
  base_fingerprint: string
  expected_routing_revision: number
  expected_state_revision: number
  day_indexes: number[]
  scope_city?: string
  mode: ConstraintMode
  as_of: string | null
  current_stop_id: string | null
  budget_cents: number | null
  day_windows: ConstraintDayWindow[]
  route_choices: ConstraintRouteChoice[]
  activities: ConstraintActivity[]
}
export interface ConstraintIntentRequest {
  schema_version: '1.0'
  base_fingerprint: string
  expected_routing_revision: number
  expected_state_revision: number
  day_indexes: number[]
  scope_city?: string
  current_day_windows: ConstraintDayWindow[]
  mode: ConstraintMode
  as_of: string | null
  instruction: string
}
export interface ConstraintIntent {
  patch: {
    budget_cents?: number
    day_windows?: ConstraintDayWindow[]
    activities?: (Pick<ConstraintActivity, 'stop_id'> & Partial<Omit<ConstraintActivity, 'stop_id'>>)[]
    route_choices?: ConstraintRouteChoice[]
    current_stop_id?: string | null
  }
  summary: string[]
  unresolved: string[]
  source: 'rules' | 'llm'
}
export interface ConstraintCheck {
  code: string
  message: string
  day_index?: number | null
  stop_id?: string | null
}
export interface ConstraintChange {
  stop_id: string
  name: string
  fields: string[]
  before_day?: number | null
  after_day?: number | null
  before_start?: string | null
  after_start?: string | null
  before_end?: string | null
  after_end?: string | null
}
export interface ConstraintPreview {
  schema_version: string
  base_plan_id: string
  outcome: 'feasible' | 'provisional' | 'unchanged' | 'conflict' | 'no_solution_found'
  preview_id?: string | null
  engine: ConstraintEngine
  summary: {
    before_cents: number
    after_cents: number
    budget_cents: number
    changed_count: number
    route_changed_count?: number
    [key: string]: unknown
  }
  changes: ConstraintChange[]
  route_changes?: {
    day_index: number; from_id: string; to_id: string; from_name: string; to_name: string
    before_mode: Mode; after_mode: Mode; before_cost_cents: number | null; after_cost_cents: number | null
  }[]
  unplaced: { stop_id: string; name: string; priority: ActivityPriority; reason: string }[]
  checks: ConstraintCheck[]
  errors: ConstraintCheck[]
  warnings: ConstraintCheck[]
  unknowns: ConstraintCheck[]
  notices: string[]
  search: Record<string, unknown>
}
export interface ConstraintApplyResponse { plan: Plan }
