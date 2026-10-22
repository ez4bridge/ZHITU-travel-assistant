import type { ConstraintApplyResponse, ConstraintContext, ConstraintIntent, ConstraintIntentRequest, ConstraintPreview, ConstraintPreviewRequest } from './constraintTypes'

export class ConstraintApiError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

async function constraintRequest<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', signal,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    const message = typeof detail === 'string' ? detail : response.status === 409
      ? '行程、路线或已确认进度已经变化。请刷新当前行程后重新预览，原方案保留。'
      : response.status === 422 ? '部分输入不符合约束，请检查日期、时间和活动选择。' : '约束调整未完成，请稍后重试。'
    throw new ConstraintApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

const planPath = (id: string) => `/api/plans/${encodeURIComponent(id)}`
export const getConstraintContext = (id: string, signal: AbortSignal) =>
  constraintRequest<ConstraintContext>(`${planPath(id)}/constraint-context`, signal)
export const previewConstraints = (id: string, body: ConstraintPreviewRequest, signal: AbortSignal) =>
  constraintRequest<ConstraintPreview>(`${planPath(id)}/constraint-preview`, signal, body)
export const parseConstraintIntent = (id: string, body: ConstraintIntentRequest, signal: AbortSignal) =>
  constraintRequest<ConstraintIntent>(`${planPath(id)}/constraint-intent`, signal, body)
export const applyConstraints = (id: string, previewId: string, signal: AbortSignal) =>
  constraintRequest<ConstraintApplyResponse>(`${planPath(id)}/constraint-apply`, signal, { preview_id: previewId })
