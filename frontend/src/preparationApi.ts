import type { PreparationSummary } from './preparationTypes'

/** Read-only derived metadata. This request never books, changes or generates an itinerary. */
export async function getPreparation(planId: string, signal: AbortSignal): Promise<PreparationSummary> {
  const response = await fetch(`/api/plans/${encodeURIComponent(planId)}/preparation`, { method: 'GET', signal })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : '暂时无法核对出行准备信息，请稍后重试。')
  if (!body || !['preview', 'needs_review', 'ready'].includes(body.state) || !Number.isInteger(body.missing_count)
    || body.missing_count < 0 || !Array.isArray(body.hotels) || !Array.isArray(body.transports)
    || !Array.isArray(body.blocking) || !Array.isArray(body.unknowns)) {
    throw new Error('出行准备信息不完整，暂不能确认行程状态，请重新核对。')
  }
  return body as PreparationSummary
}
