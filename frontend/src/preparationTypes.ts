export type PreparationState = 'preview' | 'needs_review' | 'ready'
export interface PreparationHotel {
  city: string; nights: string[]; status: 'missing' | 'partial' | 'provided' | 'located'; names: string[]
  suggestions: { label: string; basis: string; source_status: 'map_reference' | 'plan_suggestion'; source_url: string; query_url: string }[]
  query_url: string; notes: string[]
}
export interface PreparationTransport {
  index: number; from_city: string; to_city: string; travel_date: string; mode: string
  status: 'estimated' | 'queried' | 'user_provided'; number: string; departure_time: string; arrival_time: string
  reference_total_yuan: string | null
  options: { mode: string; label: string; source_status: 'queried' | 'estimate' | 'external_query'; summary: string; query_url: string }[]
  notes: string[]
}
export interface PreparationIssue {
  code: string; message: string; severity: 'block' | 'advice'
  group: string; group_title: string; count: number
  day_index: number | null; stop_id: string | null; label: string
  target: 'budget' | 'day' | 'validation'
  members?: { day_index: number | null; stop_id: string | null; label: string }[]
}
export interface PreparationSummary {
  state: PreparationState; title: string; summary: string; missing_count: number
  hotels: PreparationHotel[]; transports: PreparationTransport[]; blocking: string[]; unknowns: string[]
  issues?: PreparationIssue[]
}
