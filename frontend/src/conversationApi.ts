import { ConstraintApiError } from './constraintApi'
import type { ConstraintPreview, ConstraintRouteChoice } from './constraintTypes'

export interface DialogueMessage { role: 'user' | 'assistant'; content: string }
export interface ConversationResult {
  assistant_message: string; unresolved: string[]; proposal_summary: string[]; preview: ConstraintPreview | null
}
export interface AIAdjustContext {
  base_fingerprint: string; routing_revision: number; state_revision: number; current_plan_id: string
}
export async function getAIAdjustContext(id: string, signal: AbortSignal): Promise<AIAdjustContext> {
  const response = await fetch(`/api/plans/${encodeURIComponent(id)}/ai-adjust-context`, { signal })
  if (!response.ok) throw new ConstraintApiError('未能读取行程，请稍后重试。', response.status)
  return response.json()
}
export async function aiAdjustPreview(id: string, context: AIAdjustContext, instruction: string,
  messages: DialogueMessage[], dayIndex: number | null, routes: ConstraintRouteChoice[], signal: AbortSignal): Promise<ConversationResult> {
  const response = await fetch(`/api/plans/${encodeURIComponent(id)}/ai-adjust-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ base_fingerprint: context.base_fingerprint, expected_routing_revision: context.routing_revision,
      expected_state_revision: context.state_revision, instruction, day_index: dayIndex, route_choices: routes,
      messages: messages.slice(-10).map(m => ({ ...m, content: m.content.slice(0, 2000) })) }),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ConstraintApiError(typeof payload?.detail === 'string' ? payload.detail : '本次调整未完成；原方案保留。', response.status)
  }
  return response.json()
}
