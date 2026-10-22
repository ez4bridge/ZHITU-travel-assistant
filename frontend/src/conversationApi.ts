import { ConstraintApiError } from './constraintApi'
import type { ConstraintPreview, ConstraintPreviewRequest } from './constraintTypes'

export interface DialogueMessage { role: 'user' | 'assistant'; content: string }
export interface ConversationResult {
  assistant_message: string; unresolved: string[]; proposal_summary: string[]; preview: ConstraintPreview | null
}
export async function conversationPreview(id: string, command: ConstraintPreviewRequest, instruction: string, messages: DialogueMessage[], signal: AbortSignal): Promise<ConversationResult> {
  const response = await fetch(`/api/plans/${encodeURIComponent(id)}/conversation-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ command, instruction, messages: messages.slice(-10).map(m => ({ ...m, content: m.content.slice(0, 2000) })) }),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ConstraintApiError(typeof payload?.detail === 'string' ? payload.detail : '本次对话规划未完成；原方案保留。', response.status)
  }
  return response.json()
}
