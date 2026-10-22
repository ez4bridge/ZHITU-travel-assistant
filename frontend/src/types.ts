import type { ConfirmedHotel, BookedTransport } from './bookingTypes'
export type Mode = 'walking' | 'driving' | 'transit' | 'bicycling' | 'taxi'
export interface PlanningGuideSource { title: string; url?: string; text: string; published_date?: string | null }
export type IntercityMode = 'train' | 'flight' | 'bus' | 'driving'
export interface DestinationStay { city: string; preferred_days: number | string | null }
export interface CitySuggestion { name: string; province: string; adcode: string }
export interface MapRegion {
  status: 'available' | 'unavailable'; requested_city: string; city: string
  center: [number, number] | null; approximate: true
  adcode?: string; level?: string; source?: string; message?: string
}
export interface StudyOptions {
  topic: string; end_time: string; max_stops: number; max_walking_m: number; required_place_ids: string[]
}
export interface StudySource {
  id: string; title: string; url: string; publisher: string; publication_date: string
  retrieved_at: string; original_source_mentions: string[]; text_sha256?: string
}
export interface StudyFact { id: string; title: string; text: string; source_ids: string[]; role: string; start_char?: number | null; end_char?: number | null }
export interface StudyPolicy {
  status: 'documented_open' | 'documented_closed' | 'unknown' | 'not_visitable'
  note: string; windows: [string, string][]; last_entry: string | null; source_ids: string[]; free_admission: boolean | null
}
export interface StudyContext {
  corpus_version: string; facts: StudyFact[]; sources: StudySource[]; policies: Record<string, StudyPolicy>
  retrieval_mode?: 'legacy_structured' | 'raw_text'
  places?: Record<string, { id: string; name: string; address: string }>
}
export interface StudyStop {
  place_id: string; why_selected: string; guide: string; observation_task: string; fact_ids: string[]
}
export interface StudyCatalog {
  ready: boolean; message: string; corpus_version: string; source_count: number; fact_count: number; topics: string[]
  places: { id: string; name: string; candidate_group: string; routable: boolean }[]
  retrieval_mode?: 'raw_text'; chunk_count?: number
  retrieval?: {
    configured_mode: string; selected_mode: string; actual_mode: string
    index_ready: boolean; worker_ready: boolean; fallback_reason: string; last_seconds: number
  }
}
export interface StudyAnswer {
  status: 'supported' | 'insufficient_evidence'; message: string
  paragraphs: { text: string; fact_ids: string[] }[]; context: StudyContext | null
  usage?: QAUsage
}
export interface PhotoStudyAnswer {
  answer: { status: string; visible_summary: string; history: { text: string; source_ids: string[] }[]; observation_tip: string; unknowns: string[] }
  sources: { id: string; title: string; url: string; kind: string }[]
  usage: QAUsage
}
export interface QAUsage {
  attempted_calls: number; total_tokens?: number | null; prompt_tokens?: number; completion_tokens?: number
  usage_missing_calls?: number; reported_total_tokens?: number | null
  search_calls?: number; fetch_calls?: number; vision_calls?: number; web_cache_hits?: number
  memory_chars?: number; history_turns?: number; seconds?: number
}
export type QAResult = StudyAnswer | PhotoStudyAnswer
export type ChatResult = QAResult & { conversation_id: string; request_id: string; revision: number; turn_id: string; kind: ChatTurn['kind'] }
export interface ChatTurn {
  id: string; request_id: string; revision: number; kind: 'text' | 'photo' | 'photo_followup'
  question: string; place_name: string; created_at: string; result: QAResult
}
export interface StudyConversation {
  id: string; plan_id: string | null; revision: number; turns: ChatTurn[]; photo_context_available: boolean
}
export interface TripRequest {
  origin: string; destination: string; start_date: string; start_time: string
  days: number; style: 'relaxed' | 'balanced' | 'compact'; budget: number | string
  people: number; mode: Mode | 'auto'; preferences: string
  intercity_mode: IntercityMode; intercity_modes?: IntercityMode[]; include_origin_transport: boolean; return_to_origin: boolean
  destinations: DestinationStay[]
  study?: StudyOptions | null
  guides?: PlanningGuideSource[]; compare_modes?: boolean
  confirmed_hotel?: string
  confirmed_hotels?: ConfirmedHotel[]; booked_transports?: BookedTransport[]
  guide_mode?: 'prefer' | 'inspiration'
}
export interface Stop {
  id: string; name: string; kind: 'sight' | 'meal' | 'hotel' | 'break'
  city: string
  study?: StudyStop | null
  booking_anchor?: '' | 'hotel_departure' | 'hotel_return' | 'transport_departure' | 'transport_arrival'
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
  booking_ref?: string; commitment?: 'planned' | 'committed' | 'paid'
}
export interface DayPlan {
  date: string; title: string; stops: Stop[]; expenses: Expense[]; accommodation: string; meal_note?: string; tips?: string[]
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
  rail_option_id: string; schedule_status: 'estimated' | 'queried'; price_status: 'estimated' | 'reference' | 'user_reported'
  booking_status?: 'unconfirmed' | 'user_confirmed'
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
  study_context?: StudyContext | null
  weather?: WeatherSnapshot | null
  weather_by_city?: Record<string, WeatherSnapshot>
  adjustments?: string[]
  routing_revision?: number; schedule_baseline?: Record<string, [string, string]>
}
export interface GuideDecision {
  source_index: number; excerpt: string; place_names: string[]; status: 'adopted' | 'adjusted' | 'not_used'; stop_ids: string[]; reason: string
}
export interface WeatherDay {
  date: string; status: 'available' | 'not_covered' | 'unavailable'
  day_weather: string; night_weather: string; day_temp: number | null; night_temp: number | null
  temp_max?: number | null; temp_min?: number | null
  day_wind: string; night_wind: string; day_power: string; night_power: string
}
export interface WeatherSnapshot {
  provider: 'amap' | 'qweather'; source_url: string; requested_city: string; city: string; adcode: string
  status: 'available' | 'partial' | 'not_covered' | 'unavailable'; message: string
  failure_code?: string; failure_reason?: string; retryable?: boolean | null
  issued_at: string | null; fetched_at: string; from_cache: boolean; days: WeatherDay[]
}
export interface GuideSource {
  id: string; title: string; url: string; kind: 'official' | 'public' | 'guide'
  publication_date: string; retrieved_at: string
}
export interface PlaceGuide {
  plan_id: string; stop_id: string; place_name: string; visit_date: string
  status: 'ready' | 'partial' | 'unavailable'; message: string
  items: { category: 'overview' | 'opening' | 'visit' | 'tips'; text: string; source_id: string; evidence: string }[]
  sources: GuideSource[]; queried_at: string; usage: QAUsage
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
