export type Mode = 'walking' | 'driving'
export interface TripRequest {
  origin: string; destination: string; start_date: string; start_time: string
  days: number; style: 'relaxed' | 'balanced' | 'compact'; budget: number | string
  people: number; mode: Mode; preferences: string
}
export interface Stop {
  id: string; name: string; kind: 'sight' | 'meal' | 'hotel' | 'break'
  start: string; end: string; description: string; poi_id: string
  address: string; location: [number, number] | null; matched_name: string
  location_status: 'verified' | 'unknown'; location_note: string
}
export interface Expense {
  label: string; category: string; unit_cost: string | number; quantity: number
  basis: 'person' | 'group'; estimated: true; note: string
}
export interface DayPlan {
  date: string; title: string; stops: Stop[]; expenses: Expense[]; accommodation: string
}
export interface RouteLeg {
  day_index: number; from_id: string; to_id: string; mode: Mode; available: boolean
  distance_m: number | null; duration_s: number | null; polyline: [number, number][]; note: string; queried_at: string
}
export interface Issue { code: string; message: string; day_index: number | null; stop_id: string | null }
export interface ValidationReport {
  status: 'issues' | 'passed_with_unknowns' | 'passed'
  errors: Issue[]; warnings: Issue[]; unknowns: Issue[]
  total_cents: number; budget_cents: number; backend: string; diagnostic: string
}
export interface Plan {
  id: string; parent_id: string | null; revision: number; created_at: string
  request: TripRequest; itinerary: { title: string; summary: string; days: DayPlan[]; tips: string[] }
  routes: RouteLeg[]; validation: ValidationReport; revision_instruction: string
  expense_totals: Record<string, number>
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
