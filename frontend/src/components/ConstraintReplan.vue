<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowRight, Check, CircleAlert, LoaderCircle, SlidersHorizontal, X } from 'lucide-vue-next'
import { api } from '../api'
import { aiAdjustPreview, getAIAdjustContext, type AIAdjustContext, type DialogueMessage } from '../conversationApi'
import { applyConstraints, ConstraintApiError } from '../constraintApi'
import type { ConstraintChange, ConstraintPreview, ConstraintRouteChoice } from '../constraintTypes'
import type { Mode, Plan, RouteLeg } from '../types'

const props = defineProps<{ plan: Plan; disabled?: boolean }>()
const emit = defineEmits<{ updated: [plan: Plan]; busy: [value: boolean]; refresh: [] }>()
const opened = ref(false), working = ref(false), stale = ref(false)
const error = ref(''), feedback = ref(''), feedbackPlanId = ref('')
const requestPhase = ref<'context' | 'preview' | 'apply'>('context')
const context = ref<AIAdjustContext | null>(null), preview = ref<ConstraintPreview | null>(null)
const instruction = ref(''), chatMessages = ref<DialogueMessage[]>([]), chatIssues = ref<string[]>([])
const dayIndex = ref<number | null>(null), routeChoices = ref<ConstraintRouteChoice[]>([])
const panel = ref<HTMLElement>(), chatLog = ref<HTMLElement>(), previewPanel = ref<HTMLElement>(), applyRow = ref<HTMLElement>()
let controller: AbortController | null = null, operation = 0
const modeLabels: Record<Mode, string> = { walking: '步行', driving: '驾车', transit: '公交／地铁', bicycling: '骑行', taxi: '打车' }
const priorityLabels = { must: '必去', want: '想去', optional: '可放弃' }
const money = (value: number) => (value / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const newerVersion = computed(() => Boolean(context.value && context.value.current_plan_id !== props.plan.id))
const crossingCount = computed(() => preview.value?.changes.filter(c => c.before_day != null && c.after_day != null && c.before_day !== c.after_day).length || 0)
const removalCount = computed(() => preview.value?.changes.filter(c => c.after_day == null && c.before_day != null).length || 0)
const canApply = computed(() => Boolean(preview.value?.preview_id && !preview.value.errors.length && !stale.value && !newerVersion.value))
const outcomeTitle = computed(() => preview.value?.errors.length ? '方案仍有问题，原行程保留' : 'AI 调整方案预览')
function routeKey(route: { day_index: number; from_id: string; to_id: string }) { return `${route.day_index}:${route.from_id}:${route.to_id}` }
function abortRequest() {
  operation += 1; controller?.abort(); controller = null; working.value = false; emit('busy', false)
}
function startRequest(phase: 'context' | 'preview' | 'apply') {
  abortRequest(); controller = new AbortController(); requestPhase.value = phase
  working.value = true; emit('busy', true)
  return { token: operation, signal: controller.signal }
}
function finishRequest(token: number) {
  if (token !== operation) return
  working.value = false; controller = null; emit('busy', false)
}
function showError(reason: unknown) {
  if (reason instanceof DOMException && reason.name === 'AbortError') return
  error.value = reason instanceof Error ? reason.message : '本次调整未完成，原行程保留。'
  if (reason instanceof ConstraintApiError && reason.status === 409) { stale.value = true; preview.value = null }
}
async function loadContext() {
  const { token, signal } = startRequest('context')
  error.value = ''; stale.value = false
  try {
    const data = await getAIAdjustContext(props.plan.id, signal)
    if (token === operation) context.value = data
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
async function openWorkspace() {
  if (props.disabled) return
  opened.value = true; feedback.value = ''; context.value = null; clearConversation()
  dayIndex.value = null; routeChoices.value = []
  await nextTick(); panel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  await loadContext()
}
async function openWithInstruction(text: string, _scope: 'all' | 'day' = 'all') {
  await openWorkspace()
  if (opened.value) instruction.value = text
}
async function openWithRoute(route: RouteLeg, chosenMode: Mode) {
  await openWorkspace()
  if (!opened.value) return
  const stops = props.plan.itinerary.days[route.day_index]?.stops || []
  const from = stops.find(s => s.id === route.from_id)?.name || '上一站'
  const to = stops.find(s => s.id === route.to_id)?.name || '下一站'
  dayIndex.value = route.day_index
  routeChoices.value = [{ day_index: route.day_index, from_id: route.from_id, to_id: route.to_id, mode: chosenMode }]
  instruction.value = `只调整第${route.day_index + 1}天，将「${from}」到「${to}」改为${modeLabels[chosenMode]}，保留这两个地点，按真实路线重算时间与费用，其他日期保持。`
}
defineExpose({ openWithInstruction, openWithRoute })
function closeWorkspace() {
  const saving = working.value && requestPhase.value === 'apply'
  abortRequest(); opened.value = false
  if (saving) feedback.value = '保存请求已发出，请在“我的行程”确认是否已保存新版本。'
}
async function openLatest() {
  if (!context.value) return
  const { token, signal } = startRequest('context')
  try {
    const latest = await getAIAdjustContext(props.plan.id, signal)
    const result = await api<Plan>(`/api/plans/${encodeURIComponent(latest.current_plan_id)}`)
    if (token !== operation) return
    closeWorkspace(); emit('updated', result)
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
async function computePreview() {
  if (!context.value || !instruction.value.trim() || working.value || props.disabled || newerVersion.value || stale.value) return
  const { token, signal } = startRequest('preview')
  error.value = ''; preview.value = null; chatIssues.value = []
  const text = instruction.value.trim(), messages = chatMessages.value.slice(-8)
  chatMessages.value.push({ role: 'user', content: text })
  await nextTick(); chatLog.value?.scrollTo({ top: chatLog.value.scrollHeight, behavior: 'smooth' })
  try {
    const result = await aiAdjustPreview(props.plan.id, context.value, text, messages, dayIndex.value, routeChoices.value, signal)
    if (token !== operation) return
    chatIssues.value = result.unresolved
    chatMessages.value.push({ role: 'assistant', content: [result.assistant_message, ...result.proposal_summary, ...result.unresolved].filter(Boolean).join('\n').slice(0, 2000) })
    instruction.value = ''; preview.value = result.preview
    await nextTick()
    chatLog.value?.scrollTo({ top: chatLog.value.scrollHeight, behavior: 'smooth' })
    ;(applyRow.value || previewPanel.value)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
function clearConversation() { chatMessages.value = []; chatIssues.value = []; instruction.value = ''; preview.value = null; if (!stale.value) error.value = '' }
async function applyPreview() {
  if (!canApply.value || !preview.value?.preview_id || props.disabled || working.value) return
  const { token, signal } = startRequest('apply'); error.value = ''
  try {
    const result = await applyConstraints(props.plan.id, preview.value.preview_id, signal)
    if (token !== operation) return
    closeWorkspace(); feedback.value = '已保存调整后的新版本，旧方案保留。'
    feedbackPlanId.value = result.plan.id; emit('updated', result.plan)
  } catch (reason) { if (token === operation) showError(reason) }
  finally { finishRequest(token) }
}
function changeTime(change: ConstraintChange, before: boolean) {
  const day = before ? change.before_day : change.after_day
  if (day == null) return before ? '新增活动' : '移除活动'
  const start = before ? change.before_start : change.after_start, end = before ? change.before_end : change.after_end
  return `第 ${day + 1} 天${start ? ` ${start}${end ? `—${end}` : ''}` : ''}`
}
watch(instruction, () => { preview.value = null }, { flush: 'sync' })
watch(() => [props.plan.id, props.plan.routing_revision], () => {
  abortRequest(); opened.value = false; context.value = null; clearConversation()
  if (feedbackPlanId.value !== props.plan.id) { feedback.value = ''; feedbackPlanId.value = '' }
})
onBeforeUnmount(abortRequest)
</script>

<template>
  <div class="constraint-entry">
    <div v-if="!opened" class="entry-row"><span class="entry-icon"><SlidersHorizontal :size="20" /></span><div class="entry-copy"><strong>调整行程</strong><p>与 AI 交流修改想法，预览后再确认保存。</p></div><button class="secondary-button" :disabled="disabled" @click="openWorkspace">开始调整<ArrowRight :size="15" /></button></div>
    <p v-if="feedback && !opened" class="saved-note" role="status"><Check :size="16" />{{ feedback }}</p>
    <section v-if="opened" ref="panel" class="constraint-panel card" aria-labelledby="constraint-title">
      <div class="panel-top"><div><span class="eyebrow">MAKE ROOM FOR CHANGE</span><h3 id="constraint-title">调整行程</h3></div><button class="icon-button" aria-label="关闭调整行程" @click="closeWorkspace"><X :size="20" /></button></div>
      <p class="hint intro">直接告诉 AI 想增加、删除或改变的安排。修改先预览，确认后保存；已订酒店、交通与已完成安排继续保留。</p>
      <p v-if="working" class="loading-note" role="status"><LoaderCircle :size="16" class="spin" />{{ requestPhase === 'apply' ? '正在保存新版本…' : requestPhase === 'preview' ? 'AI 正在调整方案，并核对地点、路线与费用…' : '正在读取当前行程…' }}<button class="text-button" @click="closeWorkspace">{{ requestPhase === 'apply' ? '关闭' : '取消并关闭' }}</button></p>
      <div v-if="error" class="problem-box" role="alert"><CircleAlert :size="18" /><div><strong>原方案仍然保留</strong><p>{{ error }}</p><button v-if="stale" class="text-button" :disabled="working" @click="openLatest">打开最新行程</button><button v-else-if="!context" class="text-button" :disabled="working" @click="loadContext">重新读取</button></div></div>
      <template v-if="context">
        <div v-if="newerVersion" class="problem-box"><CircleAlert :size="18" /><div><strong>这份行程已有新版本</strong><button class="text-button" :disabled="working" @click="openLatest">打开最新版本</button></div></div>
        <form class="conversation-box" @submit.prevent="computePreview">
          <fieldset :disabled="working || disabled || newerVersion || stale">
            <h4>与 AI 一起调整行程</h4>
            <div v-if="chatMessages.length" ref="chatLog" class="dialogue-history" aria-live="polite"><article v-for="(message,index) in chatMessages" :key="index" :class="message.role"><strong>{{ message.role === 'user' ? '你' : '旅行助手' }}</strong><p>{{ message.content }}</p></article></div>
            <label class="field"><span>说说你的想法</span><textarea v-model="instruction" rows="3" maxlength="2000" aria-label="行程修改要求" placeholder="例如：把明天下午的博物馆换成外滩，逛两小时；减少步行，保留已订酒店。"></textarea></label>
            <div v-if="chatIssues.length" class="result-notes"><strong>需要补充的信息</strong><ul><li v-for="(issue,index) in chatIssues" :key="index">{{ issue }}</li></ul><p>直接在这里回复，AI 会结合上文继续调整。</p></div>
            <div class="chat-actions"><button class="primary-button" type="submit" :disabled="!instruction.trim()"><LoaderCircle v-if="working" :size="16" class="spin" /><span>{{ working ? '正在调整…' : '发送并预览调整' }}</span></button><button v-if="chatMessages.length" class="text-button" type="button" @click="clearConversation">重新开始对话</button></div>
          </fieldset>
        </form>
        <section v-if="preview" ref="previewPanel" class="preview-result" aria-labelledby="preview-title">
          <div class="preview-heading"><Check :size="21" /><div><h4 id="preview-title">{{ outcomeTitle }}</h4><p>AI 生成修改方案，结合实际路线核算时间与费用。确认后保存为新版本。</p></div></div>
          <p class="preview-unsaved"><CircleAlert :size="14" />这是预览，上面的行程还没有变化；确认无误后点下方「应用此方案」才保存为新版本。</p>
          <div class="preview-stats"><div><small>整趟费用</small><strong>¥{{ money(preview.summary.before_cents) }} <ArrowRight :size="14" /> ¥{{ money(preview.summary.after_cents) }}</strong><span>预算 ¥{{ money(preview.summary.budget_cents) }}</span></div><div><small>活动调整</small><strong>{{ preview.summary.changed_count }} 项</strong><span>其中跨天 {{ crossingCount }} 项 · 移除 {{ removalCount }} 项</span></div></div>
          <div v-if="preview.changes.length" class="changes-list"><article v-for="change in preview.changes" :key="change.stop_id"><strong>{{ change.name }}</strong><div><span>{{ changeTime(change, true) }}</span><ArrowRight :size="14" /><span>{{ changeTime(change, false) }}</span></div></article></div><p v-else class="hint">没有活动变化；费用或说明的更新仍须点击应用才保存。</p>
          <div v-if="preview.route_changes?.length" class="changes-list"><h5>市内交通变化（{{ preview.route_changes.length }} 段）</h5><article v-for="route in preview.route_changes" :key="routeKey(route)"><strong>第 {{ route.day_index + 1 }} 天 · {{ route.from_name }} → {{ route.to_name }}</strong><div><span>{{ modeLabels[route.before_mode] }} · {{ route.before_cost_cents === null ? '费用待核实' : `全员约 ¥${money(route.before_cost_cents)}` }}</span><ArrowRight :size="14" /><span>{{ modeLabels[route.after_mode] }} · {{ route.after_cost_cents === null ? '费用待核实' : `全员约 ¥${money(route.after_cost_cents)}` }}</span></div></article></div>
          <div v-if="preview.unplaced.length" class="result-notes"><h5>未安排的活动</h5><ul><li v-for="item in preview.unplaced" :key="item.stop_id"><strong>{{ item.name }}（{{ priorityLabels[item.priority] }}）</strong>：{{ item.reason }}</li></ul></div>
          <div v-if="preview.errors.length" class="result-notes errors"><h5>阻断问题</h5><ul><li v-for="(item, index) in preview.errors" :key="index">{{ item.message }}</li></ul></div>
          <div v-if="preview.warnings.length" class="result-notes"><h5>需要注意</h5><ul><li v-for="(item, index) in preview.warnings" :key="index">{{ item.message }}</li></ul></div>
          <details v-if="preview.unknowns.length" class="result-notes unknowns"><summary>{{ preview.unknowns.length }} 项待核实，展开查看</summary><ul><li v-for="(item, index) in preview.unknowns" :key="index">{{ item.message }}</li></ul></details>
          <p v-for="(notice, index) in preview.notices" :key="`preview-${index}`" class="hint">{{ notice }}</p>
          <details v-if="preview.checks.length" class="checks-detail"><summary>查看检查说明（{{ preview.checks.length }}）</summary><ul><li v-for="(item, index) in preview.checks" :key="index">{{ item.message }}</li></ul></details>
          <div class="apply-row" ref="applyRow"><button v-if="canApply" class="primary-button" :disabled="working || disabled" @click="applyPreview"><LoaderCircle v-if="working" :size="17" class="spin" /><Check v-else :size="17" />应用此方案</button><p v-else class="hint">当前预览不可应用，请调整条件后重新计算。旧行程保留。</p><small v-if="canApply">预览尚未写入行程；点「应用此方案」才保存为新版本，旧版本保留。</small></div>
        </section>
      </template>
    </section>
  </div>
</template>

<style scoped>
.constraint-entry { margin:0 0 20px; min-width:0; }
.entry-row,.saved-note,.loading-note,.chat-actions,.apply-row { display:flex; align-items:center; flex-wrap:wrap; gap:10px 16px; }
.entry-row small,.hint,.apply-row small { color:#79867e; font-size:12px; line-height:1.8; }
.constraint-panel { padding:24px; min-width:0; }
.panel-top { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
.panel-top h3 { margin:7px 0 0; font-size:20px; color:#21473d; }
.intro { margin:12px 0 20px; }
.loading-note,.saved-note { color:#3b7562; font-size:13px; }
.problem-box { display:flex; gap:10px; margin:14px 0; padding:14px; border:1px solid #ead6be; border-radius:12px; background:#fff5eb; color:#885e33; font-size:13px; }
.problem-box p { line-height:1.7; overflow-wrap:anywhere; }
.problem-box svg { flex-shrink:0; }
.conversation-box { padding:18px; border-radius:14px; background:#f5f8f1; }
.conversation-box h4 { margin:0 0 16px; color:#294f41; }
fieldset { border:0; padding:0; margin:0; min-width:0; }
textarea { width:100%; min-width:0; box-sizing:border-box; border:1px solid #dce5d5; border-radius:10px; padding:12px; resize:vertical; font:inherit; font-size:14px; line-height:1.8; background:#fff; color:#315440; }
.chat-actions { margin-top:14px; }
.chat-actions .primary-button,.apply-row .primary-button { width:auto; min-width:160px; }
.dialogue-history { display:grid; gap:10px; margin:14px 0; max-height:340px; overflow:auto; }
.dialogue-history article { padding:12px; border-radius:12px; background:#fff; max-width:95%; font-size:13px; }
.dialogue-history article.user { background:#e5eee9; justify-self:end; }
.dialogue-history p { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.8; margin:5px 0; }
.preview-result { margin-top:24px; padding-top:22px; border-top:1px solid #dce6d8; }
.preview-heading { display:flex; align-items:flex-start; gap:10px; color:#61876b; }
.preview-heading h4 { margin:0; color:#294f41; font-size:16px; }
.preview-heading p { margin:7px 0; color:#79867e; font-size:12px; line-height:1.8; }
.preview-unsaved { display:flex; align-items:center; gap:8px; padding:10px 12px; border-radius:8px; background:#fdf6e7; color:#8a6c42; font-size:12px; line-height:1.8; }
.preview-unsaved svg { flex-shrink:0; }
.preview-stats { display:grid; grid-template-columns:1.4fr 1fr; gap:12px; margin:18px 0; }
.preview-stats > div { display:grid; gap:7px; padding:15px; background:#f5f8f1; border-radius:12px; }
.preview-stats small,.preview-stats span { font-size:12px; color:#79867e; }
.preview-stats strong { display:flex; align-items:center; flex-wrap:wrap; gap:7px; color:#315c48; font-size:16px; }
.changes-list article { padding:12px 0; border-bottom:1px solid #eef1e9; }
.changes-list strong { font-size:13px; color:#3b5d4c; overflow-wrap:anywhere; }
.changes-list article > div { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-top:7px; font-size:12px; color:#79867e; }
.result-notes { margin:16px 0; padding:14px; border-radius:11px; background:#f6f8f1; font-size:12px; color:#6f7e66; }
.result-notes.errors { background:#fff1eb; color:#9b654c; }
.result-notes.unknowns { background:#fbf6e9; color:#8b7951; }
.result-notes h5 { margin:0 0 8px; font-size:13px; }
.result-notes summary { cursor:pointer; font-weight:600; }
.result-notes ul,.checks-detail ul { margin:6px 0; padding-left:18px; line-height:1.8; overflow-wrap:anywhere; }
.checks-detail { margin:16px 0; color:#79867e; font-size:12px; }
.apply-row { margin-top:20px; }
button:disabled { cursor:not-allowed; opacity:.55; }
@media(max-width:620px) { .constraint-panel { padding:17px; } .conversation-box { padding:14px; } .preview-stats { grid-template-columns:1fr; } .chat-actions .primary-button,.apply-row .primary-button { width:100%; } .entry-row small { flex-basis:100%; } }

.constraint-entry:not(:has(.constraint-panel)) { padding:21px; border:1px solid #e0e8dc; border-radius:18px; background:#fff; box-shadow:0 3px 12px #273a2810; }
.entry-icon { display:grid; place-items:center; flex:0 0 38px; height:38px; border-radius:12px; color:#4b8066; background:#eef5ec; }
.entry-copy { flex:1; min-width:135px; }
.entry-copy strong { display:block; font-size:16px; color:#315843; }
.entry-copy p { margin:5px 0 0; font-size:13px; line-height:1.7; color:#7b8c78; }
.entry-row > button { display:inline-flex; align-items:center; justify-content:center; gap:6px; padding:10px 12px; font-size:13px; }
.constraint-entry > .saved-note { padding:12px 14px; margin:16px 0 0; background:#f1f6ef; border:1px solid #dde9db; border-radius:10px; line-height:1.8; overflow-wrap:anywhere; }
@media(max-width:520px) { .constraint-entry:not(:has(.constraint-panel)) { padding:18px; } .entry-row > button { margin-left:50px; } }
</style>
