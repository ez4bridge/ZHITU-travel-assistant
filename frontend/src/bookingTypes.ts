import type { IntercityMode, Plan } from './types'

/** User supplied booking facts. These fields are not proof of a supplier query. */
export interface ConfirmedHotel {
  city: string
  name: string
  address?: string
  check_in: string
  check_out: string
  total_cost?: number | null
}

export interface BookedTransport {
  transfer_index?: number | null
  from_city: string
  to_city: string
  mode: IntercityMode
  departure_date: string
  departure_time: string
  arrival_date: string
  arrival_time: string
  number?: string
  departure_place?: string
  arrival_place?: string
  total_cost?: number | null
  departure_buffer_min?: number | null
  arrival_buffer_min?: number | null
}

export interface BookingContext {
  base_fingerprint: string
  expected_routing_revision: number
  expected_state_revision: number
  confirmed_hotels: ConfirmedHotel[]
  booked_transports: BookedTransport[]
  current_plan_id?: string
}

export interface BookingPreviewRequest {
  base_fingerprint: string
  expected_routing_revision: number
  expected_state_revision: number
  confirmed_hotels: ConfirmedHotel[]
  booked_transports: BookedTransport[]
}

export interface BookingPreview {
  status: 'ready' | 'conflict'
  preview_id?: string
  changes: string[]
  notices: string[]
  conflicts: string[]
  candidate?: Plan
}

export interface BookingApplyResponse { plan: Plan }
