import type { PreparationState, PreparationSummary } from './preparationTypes'

export function preparationState(summary: PreparationSummary): PreparationState {
  if (summary.missing_count > 0) return 'preview'
  if (summary.blocking.length) return summary.state === 'preview' ? 'preview' : 'needs_review'
  return summary.state
}
export const preparationTitles: Record<PreparationState, string> = {
  preview: '行程预览，补齐后重新核对',
  needs_review: '信息已齐，接驳／时间待核实',
  ready: '已核对行程（价格、开放仍需复核）',
}
export const hotelPreparationLabels = {
  missing: '酒店尚未填写', partial: '住宿日期尚未覆盖完整', provided: '用户提供，位置待核实', located: '用户提供，地图位置已核对',
}
export const transportPreparationLabels = {
  estimated: '规划估算，尚未选定', queried: '查询参考，尚未确认选择', user_provided: '用户已填写，订单未核验',
}
export const preparationModeLabels: Record<string, string> = { train: '铁路', flight: '飞机', bus: '长途汽车', driving: '自驾' }
export function safePreparationLink(value: string) {
  try { return ['http:', 'https:'].includes(new URL(value).protocol) ? value : '' }
  catch { return '' }
}
