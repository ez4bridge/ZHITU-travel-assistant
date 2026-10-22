import type { BookingApplyResponse, BookingContext, BookingPreview, BookingPreviewRequest } from './bookingTypes'

export class BookingApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

type WireMoney = number | string | null
type BookingContextWire = Omit<BookingContext, 'confirmed_hotels' | 'booked_transports'> & {
  confirmed_hotels: (Omit<BookingContext['confirmed_hotels'][number], 'total_cost'> & { total_cost?: WireMoney })[]
  booked_transports: (Omit<BookingContext['booked_transports'][number], 'total_cost'> & { total_cost?: WireMoney })[]
}

function normalizeMoney(value: WireMoney | undefined): number | null {
  if (value == null) return null
  const amount = typeof value === 'string' ? Number(value) : value
  if ((typeof value === 'string' && !value.trim()) || !Number.isFinite(amount) || amount < 0 || amount > 10000000) {
    throw new BookingApiError('已保存的酒店或交通费用格式异常，请核对后重试，原方案保留。', 422)
  }
  return amount
}

async function bookingRequest<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', signal,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    const message = typeof detail === 'string' ? detail : response.status === 409
      ? '行程或已确认的安排已变化，请重新打开最新版本再补充，原方案保留。'
      : response.status === 422 ? '酒店或车票信息不完整，请检查城市、日期、时间和费用。' : '本次补充没有完成，原方案保留，请稍后重试。'
    throw new BookingApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

const planPath = (id: string) => `/api/plans/${encodeURIComponent(id)}`
export async function getBookingContext(id: string, signal: AbortSignal): Promise<BookingContext> {
  const data = await bookingRequest<BookingContextWire>(`${planPath(id)}/booking-context`, signal)
  return { ...data,
    confirmed_hotels: data.confirmed_hotels.map(hotel => ({ ...hotel, total_cost: normalizeMoney(hotel.total_cost) })),
    booked_transports: data.booked_transports.map(item => ({ ...item, total_cost: normalizeMoney(item.total_cost) })),
  }
}
export const previewBookings = (id: string, body: BookingPreviewRequest, signal: AbortSignal) =>
  bookingRequest<BookingPreview>(`${planPath(id)}/booking-preview`, signal, body)
export const applyBookings = (id: string, previewId: string, signal: AbortSignal) =>
  bookingRequest<BookingApplyResponse>(`${planPath(id)}/booking-apply`, signal, { preview_id: previewId })
