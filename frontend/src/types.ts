export type Mode = 'walking' | 'driving' | 'transit' | 'bicycling' | 'taxi'
export interface GuideSource { title: string; url?: string; text: string; published_date?: string | null }
export type IntercityMode = 'train' | 'flight' | 'bus' | 'driving'
export interface DestinationStay { city: string; preferred_days: number | string | null }
export interface CitySuggestion { name: string; province: string; adcode: string }
export interface TripRequest {
  origin: string; destination: string; start_date: string; start_time: string
  days: number; style: 'relaxed' | 'balanced' | 'compact'; budget: number | string
  people: number; mode: Mode | 'auto'; preferences: string
  intercity_mode: IntercityMode; include_origin_transport: boolean; return_to_origin: boolean
  destinations: DestinationStay[]
  guides?: GuideSource[]; compare_modes?: boolean
  confirmed_hotel?: string
  guide_mode?: 'prefer' | 'inspiration'
}
export interface Stop {
  id: string; name: string; kind: 'sight' | 'meal' | 'hotel' | 'break'
  city: string
  start: string; end: string; description: string; poi_id: string
  address: string; location: [number, number] | null; matched_name: string
  location_status: 'verified' | 'unknown'; location_note: string
  place_name?: string; location_behavior?: 'poi' | 'area' | 'same_as_previous' | 'unlocated'
  area_name?: string; area_poi_id?: string; anchor_note?: string
  opening_hours_status?: 'unknown' | 'map_reference'; opening_hours?: string; opening_hours_today?: string
  opening_hours_scope?: 'place' | 'area'
  information_source?: string; information_queried_at?: string | null; average_cost_yuan?: string | number | null
  food_recommendations?: string[]; food_reference?: string; meal_period?: '' | 'breakfast' | 'lunch' | 'dinner'
}
export interface Expense {
  label: string; category: string; unit_cost: string | number; quantity: number
  basis: 'person' | 'group'; estimated: true; note: string
  stop_id?: string
}
export interface DayPlan {
  date: string; title: string; stops: Stop[]; expenses: Expense[]; accommodation: string; meal_note?: string
}
export interface RouteLeg {
  day_index: number; from_id: string; to_id: string; mode: Mode; available: boolean
  scope: 'local' | 'intercity'
  distance_m: number | null; duration_s: number | null; polyline: [number, number][]; note: string; queried_at: string
  stationary?: boolean; failure_code?: string; fare_yuan?: number | string | null
  instructions?: string[]; alternatives?: RouteOption[]
  provider_endpoint?: string; taxi_cost_yuan?: number | string | null; tolls_yuan?: number | string | null
  cost_estimate?: { low: number | string; high: number | string; basis: 'person' | 'vehicle'; method: string; assumptions: string; source_url: string } | null
  user_selected?: boolean; selection_reason?: string
}
export interface CityTransfer {
  from_city: string; to_city: string; departure_date: string; departure_time: string
  arrival_date: string; arrival_time: string; mode: IntercityMode
  unit_cost: string | number; basis: 'person' | 'group'; estimated: true; note: string
  rail_option_id: string; schedule_status: 'estimated' | 'queried'; price_status: 'estimated' | 'reference'
  train_code: string; departure_station: string; arrival_station: string
  seat_type: string; seat_availability: string; queried_at: string | null; source: string; query_note: string
}
export type RouteOption = Omit<RouteLeg, 'day_index' | 'from_id' | 'to_id' | 'queried_at' | 'alternatives'>
export interface Issue { code: string; message: string; day_index: number | null; stop_id: string | null }
export interface ValidationReport {
  status: 'issues' | 'passed_with_unknowns' | 'passed'
  errors: Issue[]; warnings: Issue[]; unknowns: Issue[]
  total_cents: number; budget_cents: number; backend: string; diagnostic: string
}
export interface Plan {
  id: string; parent_id: string | null; revision: number; created_at: string
  request: TripRequest; itinerary: { title: string; summary: string; days: DayPlan[]; tips: string[]; transfers: CityTransfer[]; guide_insights?: { source_index: number; text: string }[]; guide_decisions?: GuideDecision[] }
  routes: RouteLeg[]; validation: ValidationReport; revision_instruction: string
  expense_totals: Record<string, number>
  adjustments?: string[]
  routing_revision?: number; schedule_baseline?: Record<string, [string, string]>
}
export interface GuideDecision {
  source_index: number; excerpt: string; place_names: string[]; status: 'adopted' | 'adjusted' | 'not_used'; stop_ids: string[]; reason: string
}
export interface ToolEvent { name: string; arguments: Record<string, unknown>; summary: string }
export interface Health {
  model: string; missing: string[]
  capabilities: { planning: boolean; map: boolean; routing: boolean; validator: string }
  validator_diagnostic: string
}
export interface HistoryItem {
  id: string; title: string; destination: string; start_date: string; revision: number; status: string
}
