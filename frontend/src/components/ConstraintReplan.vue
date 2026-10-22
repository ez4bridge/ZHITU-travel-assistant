<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ArrowRight, Check, CircleAlert, Clock3, LoaderCircle, ShieldCheck, SlidersHorizontal, X } from 'lucide-vue-next'
import { api } from '../api'
import { conversationPreview, type DialogueMessage } from '../conversationApi'
import { applyConstraints, ConstraintApiError, getConstraintContext } from '../constraintApi'
import type { ActivityPriority, ActivityStatus, ConstraintActivity, ConstraintChange, ConstraintContext, ConstraintDay, ConstraintMode, ConstraintPreview, ConstraintPreviewRequest, ConstraintRoute, ConstraintStop } from '../constraintTypes'
import type { Mode, Plan, RouteLeg } from '../types'

const props = defineProps<{ plan: Plan; disabled?: boolean }>()
const emit = defineEmits<{ updated: [plan: Plan]; busy: [value: boolean]; refresh: [] }>()
const opened = ref(false), working = ref(false), error = ref(''), stale = ref(false), feedback = ref('')
const feedbackPlanId = ref('')
const requestPhase = ref<'context' | 'intent' | 'preview' | 'apply'>('context')
const context = ref<ConstraintContext | null>(null), preview = ref<ConstraintPreview | null>(null)
const mode = ref<ConstraintMode | null>(null)
const startDay = ref(0), dayCount = ref(1), budget = ref('')
const selectedCity = ref('')
const dailyWindows = reactive<Record<number, { start: string; end: string }>>({})
const instruction = ref('')
const chatMessages = ref<DialogueMessage[]>([])
const chatIssues = ref<string[]>([])
const proposalSummary = ref<string[]>([])
const pendingConversation = ref(false)
const routeChoices = reactive<Record<string, Mode | ''>>({})
const asOf = ref(''), currentStop = ref(''), progressConfirmed = ref(false)
const drafts = reactive<Record<string, ConstraintActivity>>({})
const extraMissed = reactive<Record<string, boolean>>({})
const panel = ref<HTMLElement>(), previewPanel = ref<HTMLElement>(), chatLog = ref<HTMLElement>(), applyRow = ref<HTMLElement>()
let controller: AbortController | null = null
let operation = 0

function beijingNow() {
  const shifted = new Date(Date.now() + 8 * 60 * 60 * 1000)
  return shifted.toISOString().slice(0, 16)
}
const canonicalCity = (city: string) => city.trim().replace(/市$/, '')
const money = (cents: number) => (cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
const priorityLabels: Record<ActivityPriority, string> = { must: '必去', want: '想去', optional: '可放弃' }
const statusLabels: Record<ActivityStatus, string> = { planned: '尚未确认', completed: '已完成', missed: '漏游，仍想安排', cancelled: '取消，不再安排' }
const modeLabels: Record<Mode, string> = { walking: '步行', taxi: '打车', transit: '公交 / 地铁', driving: '自驾', bicycling: '骑行' }
const chosenIndexes = computed(() => Array.from({ length: Number(dayCount.value) || 1 }, (_, offset) => Number(startDay.value) + offset))
const chosenDays = computed(() => context.value?.days.filter(day => chosenIndexes.value.includes(day.day_index)) || [])
const cityScopes = (day: ConstraintDay) => day.city_scopes || day.cities.map(city => ({ city, supported: day.supported, reason: day.reason, stop_count: day.stop_count, start_min: day.start_min, end_min: day.end_min }))
const cityOptions = computed(() => {
  const first = chosenDays.value[0]
  if (!first) return []
  const available = cityScopes(first).filter(scope => scope.supported && chosenDays.value.every(day => cityScopes(day).some(other => other.supported && canonicalCity(other.city) === canonicalCity(scope.city))))
  return available.length ? available : cityScopes(first)
})
const chosenCity = computed(() => selectedCity.value || chosenDays.value[0]?.cities[0] || '')
const windowStops = computed(() => context.value?.stops.filter(stop => chosenIndexes.value.includes(stop.day_index) && canonicalCity(stop.city) === canonicalCity(chosenCity.value)) || [])
const missedOutside = computed(() => context.value?.stops.filter(stop => !chosenIndexes.value.includes(stop.day_index) && stop.status === 'missed' && canonicalCity(stop.city) === canonicalCity(chosenCity.value)) || [])
const currentChoices = computed(() => context.value?.stops.filter(stop => canonicalCity(stop.city) === canonicalCity(chosenCity.value)) || [])
const includedStops = computed(() => [...windowStops.value, ...missedOutside.value.filter(stop => extraMissed[stop.stop_id])])
const windowRoutes = computed(() => {
  const ids = new Set(windowStops.value.map(stop => stop.stop_id))
  return context.value?.routes?.filter(route => chosenIndexes.value.includes(route.day_index) && ids.has(route.from_id) && ids.has(route.to_id)) || []
})
const newerVersion = computed(() => Boolean(context.value?.current_plan_id && context.value.current_plan_id !== props.plan.id))
const windowProblem = computed(() => {
  if (!context.value) return ''
  if (chosenDays.value.length !== Number(dayCount.value)) return '所选窗口超出行程，请减少天数或调整开始日期。'
  for (const day of chosenDays.value) {
    const scope = cityScopes(day).find(item => canonicalCity(item.city) === canonicalCity(chosenCity.value))
    if (!scope) return `第 ${day.day_index + 1} 天没有${chosenCity.value}的活动；请选择另一城市或减少连续天数。`
    if (!scope.supported) return `第 ${day.day_index + 1} 天：${scope.reason}`
  }
  if (!chosenCity.value) return '请选择要调整的城市活动段；同日其他城市和跨城交通保持原样。'
  if (includedStops.value.length > 24) return '所选活动及待补游合计超过 24 项，请缩小窗口或减少补游活动。'
  return ''
})
const crossingCount = computed(() => preview.value?.changes.filter(change => change.before_day != null && change.after_day != null && change.before_day !== change.after_day).length || 0)
const removalCount = computed(() => preview.value?.changes.filter(change => change.after_day == null && change.before_day != null).length || 0)
const canApply = computed(() => Boolean(preview.value?.preview_id && !preview.value.errors.length && !chatIssues.value.length && !stale.value && !newerVersion.value))
const outcomeTitle = computed(() => ({
  feasible: '调整预览已通过已知约束', provisional: '已有调整方案，仍有信息待核实', unchanged: '当前条件下无需改变安排',
  conflict: '这些条件存在冲突，原方案保留', no_solution_found: '有限搜索内未找到可用方案，原方案保留',
})[preview.value?.outcome || 'unchanged'])

function abortRequest() {
  operation += 1
  controller?.abort(); controller = null
  working.value = false; emit('busy', false)
}
function startRequest(phase: 'context' | 'intent' | 'preview' | 'apply' = 'context') {
  abortRequest()
  controller = new AbortController()
  requestPhase.value = phase
  working.value = true; emit('busy', true)
  return { token: operation, signal: controller.signal }
}
function finishRequest(token: number) {
  if (token !== operation) return
  working.value = false; controller = null; emit('busy', false)
}
function showError(reason: unknown) {
  if (reason instanceof DOMException && reason.name === 'AbortError') return
  error.value = reason instanceof Error ? reason.message : '本次调整没有完成，原方案保留。'
  if (reason instanceof ConstraintApiError && reason.status === 409) { stale.value = true; preview.value = null }
}
function resetDraft(data: ConstraintContext) {
  chatMessages.value = []; chatIssues.value = []; proposalSummary.value = []
  pendingConversation.value = false
  Object.keys(drafts).forEach(key => delete drafts[key])
  Object.keys(extraMissed).forEach(key => delete extraMissed[key])
  Object.keys(dailyWindows).forEach(key => delete dailyWindows[Number(key)])
  data.days.forEach(day => { dailyWindows[day.day_index] = { start: clock(day.start_min), end: clock(day.end_min) } })
  Object.keys(routeChoices).forEach(key => delete routeChoices[key])
  data.stops.forEach(stop => {
    drafts[stop.stop_id] = { stop_id: stop.stop_id, status: stop.status, priority: stop.priority,
      locked: stop.status === 'completed' || stop.locked, removable_cost: false, retain_cost: stop.retain_cost || false }
  })
  startDay.value = data.days.find(day => day.supported)?.day_index ?? data.days[0]?.day_index ?? 0
  selectedCity.value = ''
  dayCount.value = 1; budget.value = (data.budget_cents / 100).toFixed(2)
  asOf.value = beijingNow(); currentStop.value = ''; progressConfirmed.value = false
  preview.value = null; stale.value = false
}
async function loadContext() {
  const { token, signal } = startRequest()
  error.value = ''; preview.value = null
  try {
    const data = await getConstraintContext(props.plan.id, signal)
    if (token !== operation) return
    context.value = data; resetDraft(data)
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
async function openWorkspace() {
  opened.value = true; mode.value = 'requirements'; instruction.value = ''; feedback.value = ''; feedbackPlanId.value = ''
  await nextTick(); panel.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  await loadContext()
}
async function openWithInstruction(text: string, _scope: 'all' | 'day' = 'all') {
  await openWorkspace()
  instruction.value = text
}
async function openWithRoute(route: RouteLeg, chosenMode: Mode) {
  await openWorkspace()
  if (!context.value || !opened.value) return
  mode.value = 'requirements'; startDay.value = route.day_index; dayCount.value = 1
  await nextTick()
  selectedCity.value = context.value.stops.find(stop => stop.stop_id === route.to_id)?.city || chosenCity.value
  const candidate = context.value.routes.find(item => routeKey(item) === routeKey(route))
  const option = candidate?.options.find(option => option.mode === chosenMode)
  if (!candidate || (!option?.available && !candidate.can_query)) {
    error.value = '本段尚不能查询此方式，请先核实两端具体地点及当前可调整范围。'; return
  }
  routeChoices[routeKey(route)] = chosenMode
  feedbackPlanId.value = props.plan.id
  feedback.value = option?.available
    ? `拟选${modeLabels[chosenMode]}；请核对所选日期和交通费用后计算预览，尚未修改原行程。`
    : `拟查询${modeLabels[chosenMode]}；点击计算预览后先查真实路线，失败会明确说明，不会直接采用或改写原行程。`
}
defineExpose({ openWithInstruction, openWithRoute })
function startRouteRefresh() {
  emit('refresh')
  closeWorkspace()
}
function closeWorkspace() {
  if (working.value && requestPhase.value === 'apply') feedback.value = '已关闭保存请求；保存可能已完成，请重新打开工作区检查最新版本。'
  abortRequest(); opened.value = false; mode.value = null; context.value = null; preview.value = null
  error.value = ''; stale.value = false
}
async function openLatest() {
  if (!context.value) return
  const { token, signal } = startRequest()
  error.value = ''
  try {
    // Another page may have accepted a child since this workspace was opened.
    const fresh = await getConstraintContext(props.plan.id, signal)
    if (token !== operation) return
    const targetId = fresh.current_plan_id || props.plan.id
    const latest = await api<Plan>(`/api/plans/${encodeURIComponent(targetId)}`, { signal })
    if (token !== operation) return
    closeWorkspace(); emit('updated', latest)
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
function centsFromInput(text: string) {
  const match = /^(\d{1,8})(?:\.(\d{1,2}))?$/.exec(text.trim())
  if (!match) return null
  const cents = Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0'))
  return Number.isSafeInteger(cents) && cents >= 100 && cents <= 1000000000 ? cents : null
}
function minuteOfDay(value: string) {
  if (!/^\d{2}:\d{2}$/.test(value)) return -1
  const [hour = -1, minute = -1] = value.split(':').map(Number)
  return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59 ? hour * 60 + minute : -1
}
function clock(value: number) {
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}
const routeKey = (route: Pick<ConstraintRoute, 'day_index' | 'from_id' | 'to_id'>) => `${route.day_index}:${route.from_id}:${route.to_id}`
function routeDuration(seconds: number | null) { return seconds === null ? '耗时未知' : `约 ${Math.ceil(seconds / 60)} 分钟` }
function isProtected(stop: ConstraintStop) { return stop.status === 'completed' || ((stop.kind === 'hotel' || Boolean(stop.booking_anchor)) && stop.locked) }
function updateStatus(stop: ConstraintStop) {
  const draft = drafts[stop.stop_id]
  if (!draft) return
  if (draft.status === 'completed') { draft.locked = true; draft.removable_cost = false }
  if (draft.status !== 'cancelled') draft.removable_cost = false
}
async function computePreview() {
  const data = context.value
  error.value = ''; preview.value = null
  if (!data || !mode.value) return
  if (windowProblem.value) { error.value = windowProblem.value; return }
  const budgetCents = centsFromInput(budget.value)
  if (budgetCents === null) { error.value = '请填写 1 至 10000000 元的全员整趟预算，最多两位小数。'; return }
  const dayWindows = chosenDays.value.map(day => ({ day_index: day.day_index,
    start_min: minuteOfDay(dailyWindows[day.day_index]?.start || ''), end_min: minuteOfDay(dailyWindows[day.day_index]?.end || '') }))
  if (dayWindows.some(day => day.start_min < 0 || day.end_min < 0 || day.end_min <= day.start_min)) { error.value = '每一天的结束时间必须晚于开始时间，不支持跨午夜的单日窗口。'; return }
  if (mode.value === 'during' && (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(asOf.value) || !progressConfirmed.value)) {
    error.value = '请核对北京时间，并明确确认已完成、漏游和取消状态。'; return
  }
  const body: ConstraintPreviewRequest = {
    schema_version: '1.0', base_fingerprint: data.base_fingerprint, expected_routing_revision: data.routing_revision,
    expected_state_revision: data.state_revision, day_indexes: chosenIndexes.value, scope_city: chosenCity.value, mode: mode.value,
    as_of: mode.value === 'during' ? `${asOf.value}:00+08:00` : null,
    current_stop_id: mode.value === 'during' ? currentStop.value || null : null,
    budget_cents: budgetCents, day_windows: dayWindows.filter(window => {
      const original = data.days.find(day => day.day_index === window.day_index)
      const scope = original && cityScopes(original).find(item => canonicalCity(item.city) === canonicalCity(chosenCity.value))
      return (scope?.start_min ?? original?.start_min) !== window.start_min || (scope?.end_min ?? original?.end_min) !== window.end_min
    }),
    route_choices: windowRoutes.value.flatMap(route => routeChoices[routeKey(route)] ? [{ day_index: route.day_index,
      from_id: route.from_id, to_id: route.to_id, mode: routeChoices[routeKey(route)] as Mode }] : []),
    activities: includedStops.value.map(stop => ({ ...drafts[stop.stop_id]!,
      // Switching back to requirements must not silently confirm progress from a hidden editor.
      status: mode.value === 'requirements' && ['completed', 'missed'].includes(drafts[stop.stop_id]!.status) ? stop.status : drafts[stop.stop_id]!.status,
    })),
  }
  const { token, signal } = startRequest('preview')
  try {
    const userText = instruction.value.trim()
    if (userText) pendingConversation.value = true
    const text = userText || (pendingConversation.value
      ? '保留本窗口尚未应用的对话变更，并结合我当前选择的活动、时间和预算重新规划。' : '')
    const messages = chatMessages.value.slice(-8)
    if (text) chatMessages.value.push({ role: 'user', content: text })
    await nextTick()
    chatLog.value?.scrollTo({ top: chatLog.value.scrollHeight, behavior: 'smooth' })
    if (token !== operation) return
    const result = await conversationPreview(props.plan.id, body, text, messages, signal)
    if (token !== operation) return
    chatIssues.value = result.unresolved
    proposalSummary.value = result.proposal_summary
    chatMessages.value.push({ role: 'assistant', content: [result.assistant_message, ...result.proposal_summary, ...result.unresolved].join('\n').slice(0, 2000) })
    instruction.value = ''
    await nextTick()
    chatLog.value?.scrollTo({ top: chatLog.value.scrollHeight, behavior: 'smooth' })
    preview.value = result.preview
    await nextTick()
    // The apply button is the whole point of a preview; bring it into view.
    ;(applyRow.value || previewPanel.value)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
function clearConversation() {
  chatMessages.value = []; chatIssues.value = []; proposalSummary.value = []
  pendingConversation.value = false; instruction.value = ''; preview.value = null
}
function chooseActivity(stop: ConstraintStop, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  const draft = drafts[stop.stop_id]!
  if (value === 'skip') {
    if (draft.locked) {
      error.value = `「${stop.name}」已锁定，请先明确取消“锁定时间”，再选择不去。`
      ;(event.target as HTMLSelectElement).value = draft.priority
      return
    }
    draft.status = 'cancelled'
  }
  else { if (draft.status === 'cancelled') draft.status = 'planned'; draft.priority = value as ActivityPriority }
}
async function applyPreview() {
  if (!canApply.value || !preview.value?.preview_id) return
  const previewId = preview.value.preview_id
  const { token, signal } = startRequest('apply')
  error.value = ''
  try {
    const result = await applyConstraints(props.plan.id, previewId, signal)
    if (token !== operation) return
    closeWorkspace(); feedback.value = '已保存为新版本，旧方案保留；跨城交通保持，窗口外只允许迁入你明确选择补游的活动。为保留已确认约束和进度，后续修改请使用本入口；后续对话和路线选择继续沿用本次已确认信息。'
    feedbackPlanId.value = result.plan.id
    emit('updated', result.plan)
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
function dayLabel(index: number | null | undefined) {
  if (index == null) return '未安排'
  const date = context.value?.days.find(day => day.day_index === index)?.date
  return `第 ${index + 1} 天${date ? ` · ${date.slice(5)}` : ''}`
}
function changeTime(change: ConstraintChange, before: boolean) {
  const day = before ? change.before_day : change.after_day
  const start = before ? change.before_start : change.after_start
  const end = before ? change.before_end : change.after_end
  return `${dayLabel(day)}${start ? ` ${start}${end ? `—${end}` : ''}` : ''}`
}
watch(() => [props.plan.id, props.plan.routing_revision], () => {
  abortRequest(); opened.value = false; context.value = null; preview.value = null; mode.value = null
  if (feedbackPlanId.value !== props.plan.id) { feedback.value = ''; feedbackPlanId.value = '' }
})
watch([mode, startDay, dayCount, selectedCity, budget, dailyWindows, routeChoices, instruction, asOf, currentStop, progressConfirmed, drafts, extraMissed], () => {
  preview.value = null
}, { deep: true })
watch([startDay, dayCount], () => { currentStop.value = ''; progressConfirmed.value = false })
watch([chosenDays, cityOptions], () => {
  if (!cityOptions.value.some(scope => canonicalCity(scope.city) === canonicalCity(selectedCity.value))) selectedCity.value = cityOptions.value.find(scope => scope.supported)?.city || cityOptions.value[0]?.city || ''
})
watch([selectedCity, chosenDays], () => {
  currentStop.value = ''; progressConfirmed.value = false
  for (const day of chosenDays.value) {
    const scope = cityScopes(day).find(item => canonicalCity(item.city) === canonicalCity(chosenCity.value))
    if (scope) dailyWindows[day.day_index] = { start: clock(scope.start_min), end: clock(scope.end_min) }
  }
})
watch(mode, next => {
  if (next !== 'requirements') return
  // A hidden progress editor cannot confirm new completed/missed facts.
  for (const stop of context.value?.stops || []) {
    const draft = drafts[stop.stop_id]
    if (draft && ['completed', 'missed'].includes(draft.status) && draft.status !== stop.status) {
      if (draft.status === 'completed') draft.locked = stop.locked
      draft.status = stop.status
    }
  }
})
watch([mode, startDay, dayCount, selectedCity], () => {
  chatMessages.value = []; chatIssues.value = []; proposalSummary.value = []
  pendingConversation.value = false
})
watch(() => [asOf.value, currentStop.value, mode.value,
  ...includedStops.value.map(stop => drafts[stop.stop_id]?.status)], () => { progressConfirmed.value = false })
onBeforeUnmount(abortRequest)
</script>

<template>
  <div class="constraint-entry">
    <div v-if="!opened" class="entry-row">
      <button class="secondary-button" :disabled="disabled" @click="openWorkspace"><SlidersHorizontal :size="17" />调整行程</button>
      <small>需要时再调整：先预览，确认后才保存。</small>
    </div>
    <p v-if="feedback && !opened" class="saved-note" role="status"><Check :size="16" />{{ feedback }}</p>
    <section v-if="opened" ref="panel" class="constraint-panel card" aria-labelledby="constraint-title">
      <div class="panel-top"><div><span class="eyebrow">MAKE ROOM FOR CHANGE</span><h3 id="constraint-title">调整行程</h3></div><button class="icon-button" aria-label="关闭调整行程" @click="closeWorkspace"><X :size="20" /></button></div>
      <p v-if="feedback" class="saved-note" role="status"><Check :size="16" />{{ feedback }}</p>
      <p class="scope-note">选择一个城市，调整连续 1—3 天中的同城活动，每天最多 8 项、合计最多 24 项。跨城当天也可选，只调整该城市活动段，其他城市、已订交通和酒店保持。</p>
      <div class="route-only">
        <div><strong>只想更新路况与顺路餐馆？</strong><p>不改活动与顺序，原地重新查询高德路线与顺路餐馆，不产生新版本。</p></div>
        <button type="button" class="secondary-button" :disabled="disabled || working || Boolean(context && context.state_revision > 0)" @click="startRouteRefresh">只刷新路况与餐馆</button>
      </div>
      <p v-if="context && context.state_revision > 0" class="route-only-note">这份行程已有确认过的约束或途中事实，自动刷新会绕过保护，已关闭；路线会在下面的调整预览里一并更新。</p>
      <p v-if="working" class="loading-note" role="status"><LoaderCircle :size="16" class="spin" />{{ requestPhase === 'apply' ? '正在保存新版本；关闭不会撤销已经完成的保存。' : requestPhase === 'intent' ? '正在把文字整理为可确认的具体变更…' : requestPhase === 'preview' ? '正在核对约束、路线和调整方案…' : '正在读取行程与已确认进度…' }}<button class="text-button" @click="closeWorkspace">{{ requestPhase === 'apply' ? '关闭工作区' : '取消并关闭' }}</button></p>
      <div v-if="error" class="problem-box" role="alert"><CircleAlert :size="18" /><div><strong>原方案仍然保留</strong><p>{{ error }}</p><button v-if="stale" class="text-button" :disabled="working" @click="openLatest">刷新当前行程</button><button v-else class="text-button" :disabled="working" @click="loadContext">重新读取约束与进度</button></div></div>
      <template v-if="context">
        <div v-if="newerVersion" class="problem-box"><CircleAlert :size="18" /><div><strong>这份行程已有更新版本</strong><p>请先打开最新版本，再进行约束调整，避免覆盖已保存内容。</p><button class="secondary-button" :disabled="working" @click="openLatest">打开最新版本</button></div></div>
        <div v-if="!context.engine.available" class="problem-box"><ShieldCheck :size="18" /><div><strong>仓颉规划引擎暂不可用</strong><p>{{ context.engine.diagnostic || '请检查本机原生引擎。原有规划和地图功能仍可使用，本功能不会改用 Python 搜索。' }}</p><button class="text-button" :disabled="working" @click="loadContext">重新检查引擎</button></div></div>
        <div class="mode-choices" role="group" aria-label="选择调整方式">
          <button :class="{ selected: mode === 'requirements' }" :disabled="working || disabled || newerVersion" @click="mode = 'requirements'"><SlidersHorizontal :size="20" /><strong>调整需求</strong><span>修改预算、作息、必去活动或锁定安排</span></button>
          <button :class="{ selected: mode === 'during' }" :disabled="working || disabled || newerVersion" @click="mode = 'during'"><Clock3 :size="20" /><strong>途中变化</strong><span>确认进度与当前位置，只重排剩余行程</span></button>
        </div>
        <form v-if="mode" class="constraint-form" @submit.prevent="computePreview">
          <fieldset :disabled="working || disabled || newerVersion || stale">
            <section class="instruction-section conversation-box" aria-label="与AI交流调整想法">
              <h4>与 AI 一起调整行程</h4><p class="hint">告诉我想新增、删除或修改什么，也可以结合下方“不去／必去／锁定”等选择。新方案会重算未发生的预算，先预览再确认保存。</p>
              <div v-if="chatMessages.length" ref="chatLog" class="dialogue-history" aria-live="polite"><article v-for="(message,index) in chatMessages" :key="index" :class="message.role"><strong>{{ message.role === 'user' ? '你' : '旅行助手' }}</strong><p>{{ message.content }}</p></article></div>
              <p v-if="working && requestPhase === 'preview'" class="chat-working"><LoaderCircle :size="15" class="spin" />正在结合对话与当前选择核对行程，请稍候…</p>
              <label class="field"><span>说说你的想法</span><textarea v-model="instruction" rows="3" maxlength="2000" aria-label="行程修改要求" placeholder="例如：不去环球影城，改去故宫，待2小时；明天10点再出门，预算保持不变。"></textarea></label>
              <div v-if="chatIssues.length" class="chat-clarification"><strong>请补充这些关键信息</strong><ul><li v-for="(issue,index) in chatIssues" :key="index">{{ issue }}</li></ul><p>直接在同一窗口回复，助手会结合上文继续。未确认的方案不会保存。</p></div>
              <button class="secondary-button" type="button" :disabled="!instruction.trim() || working || !!windowProblem" @click="computePreview">发送想法并重新规划</button>
              <button v-if="pendingConversation" type="button" class="text-button clear-conversation" :disabled="working" @click="clearConversation">放弃本轮文字改动，仅保留下方手选条件</button>
            </section>
            <section class="form-section"><h4><span>1</span>选择调整窗口</h4>
              <div class="field-grid"><label class="field"><span>开始日期</span><select v-model.number="startDay" aria-label="调整开始日期"><option v-for="item in context.days" :key="item.day_index" :value="item.day_index">第 {{ item.day_index + 1 }} 天 · {{ item.date }} · {{ item.cities.join(' / ') || '待确认城市' }}{{ !item.supported ? '（查看原因）' : item.cities.length > 1 ? '（按城市分别调整）' : '' }}</option></select></label><label class="field"><span>连续天数</span><select v-model.number="dayCount" aria-label="连续调整天数"><option :value="1">1 天</option><option :value="2">2 天</option><option :value="3">3 天</option></select></label></div>
              <label class="field"><span>本次调整的城市</span><select v-model="selectedCity" aria-label="本次调整的城市"><option v-for="scope in cityOptions" :key="scope.city" :value="scope.city">{{ scope.city }}{{ !scope.supported ? '（查看原因）' : '' }}</option></select></label>
              <p v-if="windowProblem" class="inline-error" role="status">{{ windowProblem }}</p><p v-else class="hint">{{ chosenCity }} · {{ chosenDays.map(day => day.date).join('、') }}；只调整该城市活动。跨城到达前、出发后和同日其他城市的安排保持，时段不会越过交通时刻。其他日期仅在你明确选入漏游活动时更新该来源项。</p>
            </section>
            <section v-if="mode === 'during'" class="form-section"><h4><span>2</span>确认途中情况</h4>
              <div class="field-grid"><label class="field"><span>当前北京时间</span><input v-model="asOf" type="datetime-local" aria-label="当前北京时间" required /></label><label class="field"><span>当前位置</span><select v-model="currentStop" aria-label="确认当前位置"><option value="">尚未确认，接驳待核实</option><option v-for="stop in currentChoices" :key="stop.stop_id" :value="stop.stop_id">{{ stop.name }} · {{ stop.date.slice(5) }}</option></select></label></div>
              <p class="hint">时间过去不代表已经游玩。请在下方逐项确认；位置仅引用原行程地点，不代表实时定位。{{ currentStop ? '到下一站的接驳仍须核对路线。' : '未确认位置时，接下来第一段接驳保留待核实。' }}</p>
            </section>
            <section class="form-section"><h4><span>{{ mode === 'during' ? '3' : '2' }}</span>预算与每天的活动时间</h4>
              <label class="field budget-field"><span>全员整趟预算（元）</span><input v-model="budget" type="text" inputmode="decimal" aria-label="调整后的全员整趟预算" required /></label>
              <div v-for="item in chosenDays" :key="item.day_index" class="day-window"><strong>第 {{ item.day_index + 1 }} 天 · {{ item.date }}</strong><div class="field-grid equal-columns"><label class="field"><span>最早开始</span><input v-model="dailyWindows[item.day_index]!.start" type="time" :aria-label="`第${item.day_index + 1}天最早开始时间`" required /></label><label class="field"><span>最晚结束</span><input v-model="dailyWindows[item.day_index]!.end" type="time" :aria-label="`第${item.day_index + 1}天最晚结束时间`" required /></label></div></div>
              <p class="hint">每一天分别保留原有时段；只修改某一天，不会把同一作息套给其他天。</p>
              <p class="hint">预算包含跨城、住宿和窗口外费用。规划中不去的活动重算未发生费用；已支付、已预订费用保留，不假定退款。</p>
            </section>
            <section v-if="windowRoutes.length" class="form-section"><h4><span>{{ mode === 'during' ? '4' : '3' }}</span>比较市内交通</h4><p class="hint">可选已查路线，或对已核实的同城路段先查询其它方式。缺资料的方式不会直接当作可用；查询成功后，时间与费用一并进入约束预览。</p><details v-for="route in windowRoutes" :key="routeKey(route)" class="route-choice"><summary><strong>{{ route.from_name }} → {{ route.to_name }}</strong><span>第 {{ route.day_index + 1 }} 天 · 当前{{ modeLabels[route.mode] }}{{ routeChoices[routeKey(route)] ? ` · 拟选${modeLabels[routeChoices[routeKey(route)] as Mode]}` : '' }}</span></summary><div class="route-options"><label v-for="option in route.options" :key="option.mode" :class="{ selected: routeChoices[routeKey(route)] === option.mode, unavailable: !option.available && !route.can_query }"><input v-model="routeChoices[routeKey(route)]" type="radio" :name="`constraint-route-${routeKey(route)}`" :value="option.mode" :disabled="!option.available && !route.can_query" /><span><strong>{{ modeLabels[option.mode] }}<small v-if="option.recommended">推荐</small></strong><span>{{ option.available ? routeDuration(option.duration_s) : route.can_query ? '待查询真实路线' : '暂无可用路线' }} · {{ option.cost_cents === null ? '费用待核实' : `全员约 ¥${money(option.cost_cents)}` }}</span><small v-if="option.note">{{ option.note }}</small></span></label></div><p v-if="route.recommendation_reason" class="hint">推荐理由：{{ route.recommendation_reason }}</p><button v-if="routeChoices[routeKey(route)]" type="button" class="text-button" @click="routeChoices[routeKey(route)] = ''">不指定，保留原选择</button></details></section>
            <section class="form-section"><h4><span>{{ mode === 'during' ? (windowRoutes.length ? '5' : '4') : (windowRoutes.length ? '4' : '3') }}</span>活动优先级与保护</h4>
              <p class="hint">选择“不去”会移除该活动并重算未发生预算；必去项保留，锁定内容保留原日期和时间。</p>
              <div class="activity-list">
                <article v-for="stop in windowStops" :key="stop.stop_id" class="activity-row" :class="{ protected: isProtected(stop) }">
                  <div class="activity-name"><strong>{{ stop.name }}</strong><small>{{ dayLabel(stop.day_index) }} · {{ stop.start }}—{{ stop.end }} · 关联费用 ¥{{ money(stop.cost_cents) }}</small><span v-if="stop.status === 'completed'" class="protection-label">已确认完成，保持原样</span><span v-else-if="stop.kind === 'hotel' && stop.locked" class="protection-label">已确认酒店，默认保护</span><span v-else-if="stop.booking_anchor && stop.locked" class="protection-label">已确认交通时刻，默认保护</span><span v-else-if="mode === 'requirements' && drafts[stop.stop_id]!.status !== 'planned'" class="protection-label">{{ drafts[stop.stop_id]!.status !== stop.status ? '本轮选择（未保存）' : '已确认状态' }}：{{ statusLabels[drafts[stop.stop_id]!.status] }}</span></div>
                  <div class="activity-controls"><label v-if="mode === 'during'" class="compact-field"><span>进度</span><select v-model="drafts[stop.stop_id]!.status" :disabled="stop.status === 'completed'" :aria-label="`${stop.name}的进度`" @change="updateStatus(stop)"><option value="planned">尚未确认</option><option value="completed">已完成</option><option value="missed" :disabled="isProtected(stop) || drafts[stop.stop_id]!.locked">漏游，仍想安排</option><option value="cancelled" :disabled="isProtected(stop) || drafts[stop.stop_id]!.locked">取消，不再安排</option></select></label><label class="compact-field"><span>优先级</span><select :value="drafts[stop.stop_id]!.status === 'cancelled' ? 'skip' : drafts[stop.stop_id]!.priority" @change="chooseActivity(stop, $event)" :disabled="isProtected(stop)" :aria-label="`${stop.name}的优先级`"><option value="must">必去</option><option value="want">想去</option><option value="optional">可放弃</option><option value="skip" :disabled="drafts[stop.stop_id]!.locked">不去，不再安排</option></select></label><label class="check-label"><input v-model="drafts[stop.stop_id]!.locked" type="checkbox" :disabled="isProtected(stop) || drafts[stop.stop_id]!.status === 'completed'" />锁定时间</label></div>
                  <label v-if="!isProtected(stop) && stop.removable_cost_allowed && stop.cost_cents > 0" class="cost-confirm"><input v-model="drafts[stop.stop_id]!.retain_cost" type="checkbox" :disabled="drafts[stop.stop_id]!.status === 'completed'" />保留该项费用（已支付或取消仍收费）</label>
                </article>
              </div>
              <div v-if="missedOutside.length" class="missed-section"><h5>此前确认漏游的同城活动</h5><p class="hint">按需纳入所选窗口；只考虑剩余时间，安排不下会说明原因。</p><article v-for="stop in missedOutside" :key="stop.stop_id" class="missed-row"><label class="check-label"><input v-model="extraMissed[stop.stop_id]" type="checkbox" />补游 {{ stop.name }}<small>原第 {{ stop.day_index + 1 }} 天</small></label><label v-if="extraMissed[stop.stop_id]" class="compact-field"><span>优先级</span><select v-model="drafts[stop.stop_id]!.priority" :aria-label="`${stop.name}的补游优先级`"><option value="must">必去</option><option value="want">想去</option><option value="optional">可放弃</option></select></label></article></div>
              <label v-if="mode === 'during'" class="progress-confirm"><input v-model="progressConfirmed" type="checkbox" />我已核对北京时间、位置和上述进度；未把时间已过直接当成已完成。</label>
            </section>
            <div class="preview-actions"><button class="primary-button" type="submit" :disabled="!context.engine.available || !!windowProblem"><LoaderCircle v-if="working" :size="17" class="spin" /><ShieldCheck v-else :size="17" />{{ instruction.trim() ? '按对话与选择重新规划' : '按活动选择重新规划' }}</button><small>文字要求和活动选择共同执行；预览不改行程，确认后保存新版本。</small></div>

          </fieldset>
        </form>
        <section v-if="preview" ref="previewPanel" class="preview-result" aria-labelledby="preview-title">
          <div class="preview-heading"><ShieldCheck :size="21" /><div><h4 id="preview-title">{{ outcomeTitle }}</h4><p>{{ Object.keys(preview.search).length ? `处理流程：仓颉 ${preview.engine.version} 搜索 → Python 独立复核 → 整趟校验；遇到阻断即停止，具体结果见下方。` : '输入与进度预检已发现问题，本次未进入仓颉搜索，原方案保留。' }}</p></div></div>
          <p class="preview-unsaved"><CircleAlert :size="14" />这是预览，上面的行程还没有变化；确认无误后点下方「应用此方案」才保存为新版本。</p>
          <div class="preview-stats"><div><small>整趟费用</small><strong>¥{{ money(preview.summary.before_cents) }} <ArrowRight :size="14" /> ¥{{ money(preview.summary.after_cents) }}</strong><span>预算 ¥{{ money(preview.summary.budget_cents) }}</span></div><div><small>活动调整</small><strong>{{ preview.summary.changed_count }} 项</strong><span>其中跨天 {{ crossingCount }} 项 · 移除 {{ removalCount }} 项</span></div></div>
          <div v-if="preview.changes.length" class="changes-list"><article v-for="change in preview.changes" :key="change.stop_id"><strong>{{ change.name }}</strong><div><span>{{ changeTime(change, true) }}</span><ArrowRight :size="14" /><span>{{ changeTime(change, false) }}</span></div></article></div><p v-else class="hint">没有活动时间或日期变化；已确认事实仍须点击应用才保存。</p>
          <div v-if="preview.route_changes?.length" class="changes-list"><h5>市内交通变化（{{ preview.route_changes.length }} 段）</h5><article v-for="route in preview.route_changes" :key="routeKey(route)"><strong>第 {{ route.day_index + 1 }} 天 · {{ route.from_name }} → {{ route.to_name }}</strong><div><span>{{ modeLabels[route.before_mode] }} · {{ route.before_cost_cents === null ? '费用待核实' : `全员约 ¥${money(route.before_cost_cents)}` }}</span><ArrowRight :size="14" /><span>{{ modeLabels[route.after_mode] }} · {{ route.after_cost_cents === null ? '费用待核实' : `全员约 ¥${money(route.after_cost_cents)}` }}</span></div></article></div>
          <div v-if="preview.unplaced.length" class="result-notes"><h5>未安排的活动</h5><ul><li v-for="item in preview.unplaced" :key="item.stop_id"><strong>{{ item.name }}（{{ priorityLabels[item.priority] }}）</strong>：{{ item.reason }}</li></ul></div>
          <div v-if="preview.errors.length" class="result-notes errors"><h5>阻断问题</h5><ul><li v-for="(item, index) in preview.errors" :key="index">{{ item.message }}</li></ul></div>
          <div v-if="preview.warnings.length" class="result-notes"><h5>需要注意</h5><ul><li v-for="(item, index) in preview.warnings" :key="index">{{ item.message }}</li></ul></div>
          <details v-if="preview.unknowns.length" class="result-notes unknowns"><summary>{{ preview.unknowns.length }} 项待核实，展开查看</summary><ul><li v-for="(item, index) in preview.unknowns" :key="index">{{ item.message }}</li></ul></details>
          <p v-for="(notice, index) in preview.notices" :key="`preview-${index}`" class="hint">{{ notice }}</p>
          <details v-if="preview.checks.length" class="checks-detail"><summary>查看检查说明（{{ preview.checks.length }}）</summary><ul><li v-for="(item, index) in preview.checks" :key="index">{{ item.message }}</li></ul></details>
          <div class="apply-row" ref="applyRow"><button v-if="canApply" class="primary-button" :disabled="working || disabled" @click="applyPreview"><LoaderCircle v-if="working" :size="17" class="spin" /><Check v-else :size="17" />应用此方案</button><p v-else class="hint">当前预览不可应用，请调整条件后重新计算。旧行程保留。</p><small v-if="canApply">预览尚未写入行程；点「应用此方案」才保存为新版本，旧版本保留。</small></div>
        </section>
        <p v-for="(notice, index) in context.notices" :key="`context-${index}`" class="context-note">{{ notice }}</p>
      </template>
    </section>
  </div>
</template>

<style scoped>
.constraint-entry { margin: 0 0 20px; min-width: 0; }
.entry-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 16px; padding: 0 2px; }
.entry-row > button { padding: 13px 22px; font-size: 14px; }
.entry-row small, .scope-note, .hint, .preview-actions small, .apply-row small, .context-note { color: #79867e; font-size: 12px; line-height: 1.75; }
.constraint-panel { padding: 24px; min-width: 0; }
.panel-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.panel-top h3 { margin: 7px 0 0; font-size: 20px; color: #21473d; }
.scope-note { margin: 14px 0 18px; }
.route-only { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px 16px; margin:0 0 12px; padding:12px 14px; border:1px solid #e3ebdd; border-radius:10px; background:#f7faf3; }
.route-only strong { font-size:13px; color:#426b51; }
.route-only p { margin:4px 0 0; font-size:12px; color:#7c8b74; line-height:1.7; }
.route-only button { padding:10px 16px; font-size:13px; white-space:nowrap; }
.route-only-note { margin:0 0 14px; font-size:11px; color:#967a4e; line-height:1.7; }
.preview-unsaved { display:flex; align-items:center; gap:6px; margin:10px 0 0; padding:9px 12px; border-radius:8px; background:#fdf6e7; color:#8a6c42; font-size:12px; line-height:1.7; }
.loading-note, .saved-note { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 13px; color: #3b7562; }
.problem-box { display: flex; align-items: flex-start; gap: 10px; margin: 14px 0; padding: 14px; background: #fff5eb; border: 1px solid #ead6be; border-radius: 12px; color: #885e33; font-size: 13px; }
.problem-box > svg { flex-shrink: 0; margin-top: 2px; }
.problem-box p { margin: 5px 0; line-height: 1.7; overflow-wrap: anywhere; }
.mode-choices { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 18px 0; }
.mode-choices button { display: grid; grid-template-columns: 24px 1fr; gap: 5px 10px; text-align: left; padding: 16px; border: 1px solid #dfe6dc; border-radius: 14px; background: #fff; color: #6e8875; }
.mode-choices button strong { color: #264b3e; font-size: 15px; }
.mode-choices button span { grid-column: 2; color: #849087; font-size: 12px; line-height: 1.6; }
.mode-choices button.selected { border-color: #7d9e82; background: #f0f5ed; }
.constraint-form fieldset { margin: 0; padding: 0; border: 0; min-width: 0; }
.instruction-section { padding: 16px; border-radius: 13px; background: #f5f8f1; }
.instruction-section textarea { width: 100%; min-width: 0; box-sizing: border-box; resize: vertical; border: 1px solid #dce5d5; border-radius: 10px; padding: 11px; font: inherit; font-size: 13px; line-height: 1.7; color: #315440; background: #fff; }
.budget-field { max-width: 310px; }
.day-window { padding: 13px 0; border-bottom: 1px solid #eef1e9; }
.day-window > strong { display: block; margin: 0 0 9px; color: #57734d; font-size: 12px; }
.route-choice { margin-top: 11px; border: 1px solid #e2e8dc; border-radius: 11px; padding: 13px; font-size: 12px; color: #68805c; }
.route-choice > summary { cursor: pointer; line-height: 1.8; }
.route-choice > summary > span { display: block; color: #8b9783; font-size: 11px; }
.route-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 12px; }
.route-options > label { display: flex; gap: 7px; padding: 11px; border: 1px solid #e4e9df; border-radius: 9px; cursor: pointer; }
.route-options > label.selected { background: #edf5e7; border-color: #91ad80; }
.route-options > label.unavailable { cursor: not-allowed; opacity: .6; }
.route-options input { flex-shrink: 0; width: 15px; height: 15px; margin-top: 4px; accent-color: #719263; }
.route-options > label > span { min-width: 0; display: grid; gap: 5px; font-size: 11px; line-height: 1.6; }
.route-options strong { display: flex; flex-wrap: wrap; gap: 4px 8px; font-size: 12px; }
.route-options small { color: #87967b; font-size: 10px; }
.route-options strong small { color: #698c51; }
.form-section { margin: 22px 0; min-width: 0; }
.form-section h4 { display: flex; align-items: center; gap: 9px; margin: 0 0 14px; color: #254b40; font-size: 15px; }
.form-section h4 > span { display: inline-grid; place-items: center; width: 23px; height: 23px; border-radius: 50%; background: #edf3e9; font-size: 11px; color: #719277; }
.field-grid { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); gap: 12px; }
.field-grid.three-columns { grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr); }
.field-grid.equal-columns { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.constraint-panel .field { min-width: 0; margin-bottom: 0; }
.constraint-panel select, .constraint-panel input:not([type=checkbox]):not([type=radio]) { width: 100%; min-width: 0; max-width: 100%; box-sizing: border-box; border: 1px solid #e2e8de; border-radius: 10px; background: #fbfcf9; padding: 10px; color: #294e43; font-size: 13px; min-height: 42px; }
.hint { margin: 10px 0 0; }
.inline-error { margin: 10px 0 0; color: #a26740; font-size: 13px; }
.activity-list { display: grid; gap: 10px; margin-top: 14px; }
.activity-row { padding: 14px; border: 1px solid #e4e9df; border-radius: 12px; min-width: 0; }
.activity-row.protected { background: #f7f9f4; }
.activity-name { display: grid; gap: 5px; color: #2b5143; }
.activity-name strong { font-size: 14px; overflow-wrap: anywhere; }
.activity-name small { font-size: 11px; color: #89958b; line-height: 1.7; }
.protection-label { font-size: 11px; color: #6b8d70; }
.activity-controls { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 14px; margin-top: 12px; }
.compact-field { display: grid; gap: 5px; min-width: 110px; flex: 1; max-width: 190px; font-size: 11px; color: #829082; }
.compact-field select { min-height: 36px; padding: 7px 9px; font-size: 12px; }
.check-label, .cost-confirm, .progress-confirm { display: flex; align-items: flex-start; gap: 7px; font-size: 12px; line-height: 1.7; color: #5e7765; }
.check-label { align-items: center; min-height: 36px; }
.check-label input, .cost-confirm input, .progress-confirm input { flex-shrink: 0; width: 15px; height: 15px; margin: 3px 0 0; accent-color: #658e70; }
.cost-confirm { margin-top: 13px; color: #899184; font-size: 11px; }
.progress-confirm { margin-top: 18px; padding: 13px; border-radius: 10px; background: #edf3e8; }
.missed-section { margin-top: 20px; }
.missed-section h5, .result-notes h5 { margin: 0 0 8px; color: #3b5d4c; font-size: 13px; }
.missed-row { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; padding: 12px 0; border-bottom: 1px solid #eef0e9; }
.missed-row small { color: #8a9789; }
.preview-actions, .apply-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.preview-actions .primary-button, .apply-row .primary-button { width: auto; min-width: 170px; }
.preview-result { margin-top: 24px; padding-top: 23px; border-top: 1px solid #dce6d8; scroll-margin-top: 24px; }
.preview-heading { display: flex; align-items: flex-start; gap: 10px; color: #61876b; }
.preview-heading h4 { margin: 0; font-size: 16px; color: #294f41; }
.preview-heading p { margin: 7px 0 0; color: #8a968a; font-size: 11px; line-height: 1.7; }
.preview-stats { display: grid; grid-template-columns: 1.4fr 1fr; gap: 12px; margin: 18px 0; }
.preview-stats > div { display: grid; gap: 7px; border-radius: 12px; padding: 15px; background: #f5f8f1; }
.preview-stats small, .preview-stats span { color: #87947f; font-size: 11px; }
.preview-stats strong { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; font-size: 16px; color: #315c48; }
.changes-list article { padding: 12px 0; border-bottom: 1px solid #eef1e9; }
.changes-list strong { font-size: 13px; color: #3b5d4c; }
.changes-list article > div { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 7px; font-size: 12px; color: #82917f; }
.result-notes { margin: 16px 0; padding: 14px; border-radius: 11px; background: #f6f8f1; font-size: 12px; color: #6f7e66; }
.result-notes.errors { background: #fff1eb; color: #9b654c; }
.result-notes.unknowns { background: #fbf6e9; color: #8b7951; }
.result-notes summary { cursor: pointer; font-weight: 600; }
.result-notes[open] > ul { margin-top: 10px; }
.result-notes ul, .checks-detail ul { margin: 0; padding-left: 18px; line-height: 1.8; overflow-wrap: anywhere; }
.checks-detail { margin: 16px 0; color: #85927d; font-size: 12px; line-height: 1.8; }
.apply-row { margin-top: 20px; }
.context-note { margin: 14px 0 0; font-size: 11px; }
button:disabled { cursor: not-allowed; opacity: .55; }
@media (max-width: 620px) {
  .constraint-panel { padding: 17px; }
  .mode-choices { grid-template-columns: 1fr; }
  .field-grid, .field-grid.three-columns { grid-template-columns: 1fr; }
  .field-grid.equal-columns { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .route-options { grid-template-columns: 1fr; }
  .preview-stats { grid-template-columns: 1fr; }
  .activity-row { padding: 12px; }
  .activity-controls { gap: 10px; }
  .compact-field { min-width: 100px; max-width: none; }
  .preview-actions .primary-button, .apply-row .primary-button { width: 100%; }
  .entry-row small { flex-basis: 100%; }
}
</style>

<style scoped>
.dialogue-history{display:grid;gap:10px;margin:14px 0;max-height:340px;overflow:auto}.dialogue-history article{padding:12px;border-radius:12px;background:#fff;max-width:95%}.dialogue-history article.user{background:#e5eee9;justify-self:end}.dialogue-history p{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.7;margin:5px 0}.conversation-box>button{margin-top:10px}
.chat-clarification{padding:12px;margin:12px 0;border-radius:10px;background:#fff6e8;color:#957340;font-size:12px;line-height:1.8;overflow-wrap:anywhere}.chat-clarification ul{padding-left:18px;margin:6px 0}.chat-clarification p{margin:6px 0 0}
.chat-working{display:flex;align-items:center;gap:8px;font-size:12px;color:#6b8b65;margin:10px 0}
</style>
