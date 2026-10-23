<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowRight, BedDouble, ChevronDown, CircleAlert, LoaderCircle, Plus, Sparkles, Ticket, Trash2, X } from 'lucide-vue-next'
import HotelFields from './HotelFields.vue'
import { api } from '../api'
import { BookingApiError, getBookingContext, regenerateBookings } from '../bookingApi'
import type { BookedTransport, BookingContext, BookingPreview, ConfirmedHotel } from '../bookingTypes'
import type { IntercityMode, Plan, Stop } from '../types'

const props = defineProps<{ plan: Plan; disabled?: boolean; hideEntry?: boolean }>()
const emit = defineEmits<{ applied: [plan: Plan]; busy: [value: boolean] }>()
const opened = ref(false), working = ref(false), error = ref(''), stale = ref(false)
const phase = ref<'context' | 'preview' | 'regenerate' | 'apply' | 'latest'>('context')
const context = ref<BookingContext | null>(null), preview = ref<BookingPreview | null>(null)
const hotels = ref<ConfirmedHotel[]>([]), transports = ref<BookedTransport[]>([])
const confirmed = ref(false), panel = ref<HTMLElement>(), resultPanel = ref<HTMLElement>()
const restored = ref(false)
const serverDraft = ref<{ hotels: ConfirmedHotel[]; transports: BookedTransport[] } | null>(null)
let suspendSave = false
let controller: AbortController | null = null
let operation = 0

const modes: { value: IntercityMode; label: string }[] = [
  { value: 'train', label: '铁路（高铁 / 火车）' }, { value: 'flight', label: '飞机' },
  { value: 'bus', label: '长途汽车' }, { value: 'driving', label: '自驾' },
]
const cities = computed(() => {
  const destinations = props.plan.request.destinations || []
  const names = destinations.length ? destinations.map(item => item.city) : [props.plan.request.destination]
  return [...new Set(names.map(city => city?.trim()).filter((city): city is string => Boolean(city)))]
})
const journeyCities = computed(() => [...new Set([props.plan.request.origin, ...cities.value].filter(Boolean))])
const newerVersion = computed(() => Boolean(context.value?.current_plan_id && context.value.current_plan_id !== props.plan.id))
const busyText = computed(() => ({ context: '正在读取已确认信息…', preview: '正在按新酒店与交通重排接驳、景点和用餐，并重算时间与预算…', regenerate: '正在按新条件请模型整份重新生成（通常 1–2 分钟，请勿关闭页面）…', apply: '正在保存重新规划的新版本，原版保留…', latest: '正在打开最新版本…' })[phase.value])
const originalStops = computed(() => new Map(props.plan.itinerary.days.flatMap(day =>
  day.stops.map(stop => [stop.id, { date: day.date, stop }] as const))))
function scheduleChange(date: string, stop: Stop) {
  const original = originalStops.value.get(stop.id)
  if (!original) return '新增安排'
  if (original.date !== date) return `原为 ${original.date} ${original.stop.start}—${original.stop.end}`
  if (original.stop.start !== stop.start || original.stop.end !== stop.end) return `原为 ${original.stop.start}—${original.stop.end}`
  if (original.stop.name !== stop.name) return `原为 ${original.stop.name}`
  return ''
}
const money = (cents: number) => (cents / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const inputValue = (event: Event) => (event.target as HTMLInputElement).value
const optionalNumber = (event: Event) => { const value = inputValue(event); return value.trim() ? Number(value) : null }

function invalidate() { preview.value = null; confirmed.value = false; error.value = '' }
const draftKey = () => `booking-draft-v1:${props.plan.id}`
function readDraft() {
  try {
    const raw = localStorage.getItem(draftKey())
    if (!raw) return null
    const value = JSON.parse(raw) as { fingerprint?: unknown; hotels?: unknown; transports?: unknown }
    if (typeof value.fingerprint !== 'string' || !Array.isArray(value.hotels) || !Array.isArray(value.transports)) return null
    return { fingerprint: value.fingerprint, hotels: value.hotels as ConfirmedHotel[], transports: value.transports as BookedTransport[] }
  } catch { return null }
}
function saveDraft() {
  if (suspendSave || !context.value) return
  try {
    localStorage.setItem(draftKey(), JSON.stringify({ fingerprint: context.value.base_fingerprint,
      hotels: hotels.value, transports: transports.value }))
  } catch { /* storage may be unavailable or full; the form still works */ }
}
function clearDraft() {
  try { localStorage.removeItem(draftKey()) } catch { /* ignore */ }
  restored.value = false
  if (!serverDraft.value) return
  suspendSave = true
  hotels.value = serverDraft.value.hotels.map(hotel => ({ ...hotel }))
  transports.value = serverDraft.value.transports.map(transport => ({ ...transport }))
  suspendSave = false
}
watch([hotels, transports], () => { invalidate(); saveDraft() }, { deep: true, flush: 'sync' })

function abort() {
  operation += 1
  controller?.abort(); controller = null
  working.value = false; emit('busy', false)
}
function begin(nextPhase: typeof phase.value) {
  abort(); controller = new AbortController(); phase.value = nextPhase
  working.value = true; emit('busy', true)
  return { token: operation, signal: controller.signal }
}
function finish(token: number) {
  if (operation !== token) return
  controller = null; working.value = false; emit('busy', false)
}
function showError(reason: unknown) {
  if (reason instanceof DOMException && reason.name === 'AbortError') return
  error.value = reason instanceof Error ? reason.message : '本次补充未完成，原方案保留。'
  if (reason instanceof BookingApiError && reason.status === 409) { stale.value = true; preview.value = null }
}
async function open() {
  opened.value = true; context.value = null; preview.value = null; error.value = ''; stale.value = false
  await nextTick(); panel.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  const { token, signal } = begin('context')
  try {
    const data = await getBookingContext(props.plan.id, signal)
    if (operation !== token) return
    context.value = data
    serverDraft.value = {
      hotels: data.confirmed_hotels.map(hotel => ({ ...hotel })),
      transports: data.booked_transports.map(transport => ({ ...transport })),
    }
    const draft = readDraft()
    if (draft && draft.fingerprint === data.base_fingerprint && (draft.hotels.length || draft.transports.length)) {
      hotels.value = draft.hotels.map(hotel => ({ ...hotel }))
      transports.value = draft.transports.map(transport => ({ ...transport }))
      restored.value = true
    } else {
      suspendSave = true
      hotels.value = serverDraft.value.hotels.map(hotel => ({ ...hotel }))
      transports.value = serverDraft.value.transports.map(transport => ({ ...transport }))
      suspendSave = false
      if (draft) clearDraft()
      restored.value = false
    }
    confirmed.value = false
  } catch (reason) { if (operation === token) showError(reason) }
  finally { finish(token) }
}
function close() {
  if (working.value && phase.value === 'apply') return
  abort(); opened.value = false; context.value = null; preview.value = null; error.value = ''
}
defineExpose({ open })

function addTransport() {
  if (transports.value.length >= 14) return
  const unused = props.plan.itinerary.transfers.findIndex((_, index) => !transports.value.some(item => item.transfer_index === index))
  const transfer = props.plan.itinerary.transfers[unused]
  transports.value = [...transports.value, {
    transfer_index: unused >= 0 ? unused : null,
    from_city: transfer?.from_city || props.plan.request.origin || '',
    to_city: transfer?.to_city || cities.value[0] || '', mode: transfer?.mode || 'train',
    departure_date: transfer?.departure_date || props.plan.request.start_date, departure_time: '',
    arrival_date: transfer?.arrival_date || props.plan.request.start_date, arrival_time: '',
    number: '', departure_place: '', arrival_place: '', total_cost: null,
    departure_buffer_min: null, arrival_buffer_min: null,
  }]
}
function selectTransfer(index: number, event: Event) {
  const value = inputValue(event)
  const selected = value === '' ? null : Number(value)
  const original = selected === null ? null : props.plan.itinerary.transfers[selected]
  transports.value = transports.value.map((transport, current) => current !== index ? transport : {
    ...transport, transfer_index: selected,
    ...(original ? { from_city: original.from_city, to_city: original.to_city, mode: original.mode,
      departure_date: original.departure_date, arrival_date: original.arrival_date } : {}),
    // Supplier estimates from the old plan must never become confirmed booking times.
    departure_time: '', arrival_time: '', number: '', departure_place: '', arrival_place: '', total_cost: null,
  })
}
function removeTransport(index: number) { transports.value = transports.value.filter((_, current) => current !== index) }
function transferLabel(index: number) {
  const item = props.plan.itinerary.transfers[index]
  return item ? `第 ${index + 1} 段 · ${item.from_city} → ${item.to_city}` : '未关联原交通'
}
function validateDraft() {
  for (const [index, hotel] of hotels.value.entries()) {
    if (!hotel.city.trim() || !hotel.name.trim() || !hotel.check_in || !hotel.check_out) return `请补全酒店 ${index + 1} 的城市、名称、入住和退房日期。`
    if (hotel.check_out <= hotel.check_in) return `酒店 ${index + 1} 的退房日期需要晚于入住日期。`
    if (hotel.total_cost != null && (!Number.isFinite(hotel.total_cost) || hotel.total_cost < 0)) return `酒店 ${index + 1} 的总房费需要是非负金额，不清楚请留空。`
  }
  for (const [index, item] of transports.value.entries()) {
    if (!item.from_city.trim() || !item.to_city.trim() || !item.departure_date || !item.arrival_date || !item.departure_time || !item.arrival_time) return `请补全已订交通 ${index + 1} 的城市及实际出发、到达时间。`
    if (`${item.arrival_date}T${item.arrival_time}` <= `${item.departure_date}T${item.departure_time}`) return `已订交通 ${index + 1} 的到达时间需要晚于出发时间，跨夜请修改到达日期。`
    if (item.total_cost != null && (!Number.isFinite(item.total_cost) || item.total_cost < 0)) return `已订交通 ${index + 1} 的全员总费用需要是非负金额，不清楚请留空。`
  }
  return ''
}
function revealInvalid(event: Event) {
  const input = event.target as HTMLInputElement
  let parent = input.parentElement
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
    parent = parent.parentElement
  }
  error.value = `请检查${input.getAttribute('aria-label') || '标出的信息'}，补全必填项并使用有效日期、时间或金额。`
}
async function regenerate() {
  error.value = validateDraft()
  if (error.value || !context.value || !confirmed.value || newerVersion.value || stale.value) return
  preview.value = null
  const { token, signal } = begin('regenerate')
  try {
    const body = {
      base_fingerprint: context.value.base_fingerprint,
      expected_routing_revision: context.value.expected_routing_revision,
      expected_state_revision: context.value.expected_state_revision,
      confirmed_hotels: hotels.value.map(hotel => ({ ...hotel })),
      booked_transports: transports.value.map(item => ({ ...item })),
    }
    const result = await regenerateBookings(props.plan.id, body, signal)
    if (operation !== token) return
    if (result.status === 'applied' && result.plan) {
      // Full regeneration saves a new version directly; nothing to confirm.
      clearDraft(); opened.value = false; preview.value = null
      emit('applied', result.plan)
      return
    }
    preview.value = result as unknown as BookingPreview
    await nextTick(); resultPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } catch (reason) { if (operation === token) showError(reason) }
  finally { finish(token) }
}
async function openLatest() {
  if (!context.value?.current_plan_id) return
  const id = context.value.current_plan_id
  const { token, signal } = begin('latest')
  try {
    const latest = await api<Plan>(`/api/plans/${encodeURIComponent(id)}`, { signal })
    if (operation !== token) return
    opened.value = false; preview.value = null; emit('applied', latest)
  } catch (reason) { if (operation === token) showError(reason) }
  finally { finish(token) }
}
watch(() => props.plan.id, () => { abort(); opened.value = false; context.value = null; preview.value = null; error.value = '' })
onBeforeUnmount(abort)
</script>

<template>
  <div v-if="!opened" v-show="!hideEntry" class="booking-entry">
    <button type="button" :disabled="disabled" @click="open"><BedDouble :size="16" /> 补充酒店与已订交通</button>
    <small>填入已选酒店和交通，重新规划接驳、景点、用餐与费用；确认后保存新版本。</small>
  </div>
  <section v-else ref="panel" class="booking-panel card" aria-labelledby="booking-title">
    <header class="booking-heading"><div><span class="booking-eyebrow">SETTLE THE DETAILS</span><h3 id="booking-title">补充酒店与已订交通</h3></div><button type="button" :disabled="working && phase === 'apply'" aria-label="关闭酒店与交通补录" @click="close"><X :size="19" /></button></header>
    <p class="booking-intro">按你填写的酒店位置、车站／机场与到离时间重新安排：先计划到站接驳和酒店，再调整景点、用餐、时间及费用。点一次就会带着这些固定事实整份重新生成，成功即保存为新版本（旧版本保留在“我的行程”），不再要求二次确认。有已完成或锁定的安排时，整份重新生成会被拒绝，避免丢失事实。填写内容标为用户提供，不代表酒店或票务官方查验。</p>
    <p v-if="restored" class="booking-restored" role="status">已恢复上次未保存的填写（只保存在本浏览器，未提交）。<button type="button" @click="clearDraft">清除草稿</button></p>
    <p v-if="working" class="booking-progress" role="status"><LoaderCircle class="spinning" :size="17" /> {{ busyText }}</p>
    <div v-if="error" class="booking-problem" role="alert"><CircleAlert :size="17" /><div><p>{{ error }}</p><button v-if="stale" type="button" :disabled="working" @click="open">重新读取最新状态 <ArrowRight :size="14" /></button></div></div>
    <div v-if="newerVersion" class="booking-problem"><CircleAlert :size="17" /><div><p>当前已有更新版本，请打开最新行程后再补充，避免覆盖已确认的安排。</p><button type="button" :disabled="working" @click="openLatest">打开最新版本 <ArrowRight :size="14" /></button></div></div>
    <button v-if="!context && !working" type="button" class="booking-secondary" @click="open">重新读取</button>
    <form v-if="context" class="booking-form" @submit.prevent="regenerate()" @invalid.capture="revealInvalid">
      <fieldset :disabled="working || disabled || stale || newerVersion">
        <section class="booking-section"><h4><BedDouble :size="18" /> 已确认的住宿</h4><HotelFields v-model="hotels" :cities="cities" :start-date="plan.request.start_date" :days="plan.request.days" :disabled="working || disabled || stale || newerVersion" /></section>
        <section class="booking-section">
          <h4><Ticket :size="18" /> 已订车票 / 机票</h4>
          <p class="booking-help">只填写已经选定或预订的交通。时间采用出发、到达当地日期与钟点（国内北京时间）；不需要姓名、证件号或订单号。</p>
          <datalist id="booking-transit-cities"><option v-for="city in journeyCities" :key="city" :value="city" /></datalist>
          <article v-for="(item, index) in transports" :key="index" class="transport-row">
            <header><strong>已订交通 {{ index + 1 }}</strong><button type="button" :aria-label="`移除已订交通 ${index + 1}`" @click="removeTransport(index)"><Trash2 :size="15" /> 移除</button></header>
            <div class="booking-grid">
              <label class="full-width"><span>对应原行程的哪段交通</span><select :value="item.transfer_index ?? ''" :aria-label="`已订交通 ${index + 1} 对应行程段`" @change="selectTransfer(index, $event)"><option value="">按填写的起讫城市匹配</option><option v-for="(_transfer, transferIndex) in plan.itinerary.transfers" :key="transferIndex" :value="transferIndex">{{ transferLabel(transferIndex) }}</option></select></label>
              <label><span>交通方式</span><select v-model="item.mode" :aria-label="`已订交通 ${index + 1} 方式`"><option v-for="mode in modes" :key="mode.value" :value="mode.value">{{ mode.label }}</option></select></label>
              <label><span>车次 / 航班号（可选）</span><input v-model="item.number" :aria-label="`已订交通 ${index + 1} 车次或航班号`" maxlength="30" placeholder="如 G123、CA1234" /></label>
              <label><span>出发城市</span><input v-model="item.from_city" :aria-label="`已订交通 ${index + 1} 出发城市`" list="booking-transit-cities" maxlength="40" required /></label>
              <label><span>到达城市</span><input v-model="item.to_city" :aria-label="`已订交通 ${index + 1} 到达城市`" list="booking-transit-cities" maxlength="40" required /></label>
              <label><span>出发车站 / 机场（可选）</span><input v-model="item.departure_place" :aria-label="`已订交通 ${index + 1} 出发车站或机场`" maxlength="120" placeholder="准确名称有助核对接驳" /></label>
              <label><span>到达车站 / 机场（可选）</span><input v-model="item.arrival_place" :aria-label="`已订交通 ${index + 1} 到达车站或机场`" maxlength="120" placeholder="如北京南站、首都国际机场" /></label>
              <label><span>出发日期</span><input v-model="item.departure_date" :aria-label="`已订交通 ${index + 1} 出发日期`" type="date" required /></label>
              <label><span>实际出发时间</span><input v-model="item.departure_time" :aria-label="`已订交通 ${index + 1} 出发时间`" type="time" required /></label>
              <label><span>到达日期</span><input v-model="item.arrival_date" :aria-label="`已订交通 ${index + 1} 到达日期`" type="date" :min="item.departure_date" required /></label>
              <label><span>实际到达时间</span><input v-model="item.arrival_time" :aria-label="`已订交通 ${index + 1} 到达时间`" type="time" required /></label>
              <label class="full-width"><span>这段已订交通全员总费用（元，可选）</span><input type="number" :value="item.total_cost ?? ''" :aria-label="`已订交通 ${index + 1} 全员总费用`" min="0" max="10000000" step="0.01" placeholder="留空保留原估算；0 表示确认免费" @input="item.total_cost = optionalNumber($event)" /></label>
            </div>
            <details class="buffer-settings"><summary>接驳与进站缓冲 <ChevronDown :size="13" /></summary><p class="booking-help">默认提前30分钟到火车站、90分钟到机场。铁路到达不另加统一等待；飞机另留60分钟出站／取行李。每日车站节点另显示5分钟接驳准备，后续市内路程单算；这些是规划估算，可按实际修改，不代表官方最短要求。</p><div class="booking-grid"><label><span>提前到站 / 机场（分钟）</span><input type="number" :value="item.departure_buffer_min ?? ''" :aria-label="`已订交通 ${index + 1} 出发缓冲分钟`" min="0" max="240" step="1" placeholder="默认值" @input="item.departure_buffer_min = optionalNumber($event)" /></label><label><span>到达后额外预留（分钟）</span><input type="number" :value="item.arrival_buffer_min ?? ''" :aria-label="`已订交通 ${index + 1} 到达缓冲分钟`" min="0" max="240" step="1" :placeholder="item.mode === 'flight' ? '默认60分钟' : '默认0分钟'" @input="item.arrival_buffer_min = optionalNumber($event)" /></label></div></details>
          </article>
          <button type="button" class="booking-add" :disabled="transports.length >= 14" @click="addTransport"><Plus :size="15" /> 添加已订交通</button>
        </section>
        <p class="booking-help">酒店总房费与交通全员总费用都只记一次。未填价格保留估算并待核实；移除记录不代表退款。无法满足时会列出具体冲突，填写的信息仍保留，可直接修改后重算。</p>
        <label class="booking-confirm"><input v-model="confirmed" type="checkbox" required /><span>我已核对以上酒店日期和交通时刻，按这些信息重新安排；原方案先保留。</span></label>
        <button type="submit" class="primary-button" :disabled="!confirmed || working || disabled || stale || newerVersion"><LoaderCircle v-if="working && phase === 'regenerate'" class="spinning" :size="16" /><Sparkles v-else :size="16" /> {{ working && phase === 'regenerate' ? '正在整份重新生成并保存…' : '按新条件整份重新生成并保存' }}</button>
        <p class="booking-help">带着这些固定事实重新请模型安排每天景点、用餐与接驳，通常 1–2 分钟；成功后直接保存为新版本（旧版本保留），不需要再确认。</p>
      </fieldset>
    </form>
    <section v-if="preview" ref="resultPanel" class="booking-result" aria-live="polite">
      <h4><CircleAlert :size="19" /> 本次重新生成没有完成</h4>
      <p class="booking-help">原方案未改动；下面是仍需要处理的冲突与提示。</p>
      <div v-if="preview.candidate" class="booking-totals"><span>整趟费用（全员）</span><strong>¥{{ money(plan.validation.total_cents) }} <ArrowRight :size="15" /> ¥{{ money(preview.candidate.validation.total_cents) }}</strong></div>
      <div v-if="preview.changes.length" class="booking-notes"><strong>将发生的变化</strong><ul><li v-for="(change, index) in preview.changes" :key="index">{{ change }}</li></ul></div>
      <div v-if="preview.conflicts.length" class="booking-notes conflicts"><strong>需要处理的冲突</strong><ul><li v-for="(conflict, index) in preview.conflicts" :key="index">{{ conflict }}</li></ul></div>
      <div v-if="preview.notices.length" class="booking-notes"><strong>请留意</strong><ul><li v-for="(notice, index) in preview.notices" :key="index">{{ notice }}</li></ul></div>
      <details v-if="preview.candidate" class="candidate-days" open><summary>重新安排的每日接驳、酒店、用餐与景点</summary><article v-for="(day, dayIndex) in preview.candidate.itinerary.days" :key="day.date"><strong>第 {{ dayIndex + 1 }} 天 · {{ day.date }} · {{ day.title }}</strong><ol><li v-for="stop in day.stops" :key="stop.id"><time>{{ stop.start }}—{{ stop.end }}</time><span>{{ stop.name }}<small v-if="stop.kind === 'hotel'">酒店</small><small v-else-if="stop.kind === 'meal'">用餐</small><small v-else-if="stop.booking_anchor?.startsWith('transport_')">车站／机场</small></span><small v-if="scheduleChange(day.date, stop)" class="schedule-change">{{ scheduleChange(day.date, stop) }}</small></li></ol></article></details>
      <details v-if="preview.candidate && preview.candidate.validation.unknowns.length" class="booking-notes"><summary>仍待核实的信息（{{ preview.candidate.validation.unknowns.length }}）</summary><ul><li v-for="(issue, index) in preview.candidate.validation.unknowns" :key="index">{{ issue.message }}</li></ul></details>
    </section>
  </section>
</template>

<style scoped>
.booking-entry { display: flex; align-items: center; flex-wrap: wrap; gap: 11px; margin: 0 0 18px; }
.booking-entry > button { display: inline-flex; align-items: center; gap: 8px; padding: 10px 12px; border: 1px solid #d9e7d0; border-radius: 10px; color: #577e46; background: #f7faf1; font-size: 12px; }
.booking-entry > small { flex: 1; min-width: 200px; font-size: 11px; line-height: 1.8; color: #8a957e; }
.booking-panel { padding: 24px; margin: 0 0 20px; min-width: 0; scroll-margin-top: 24px; }
.booking-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; }
.booking-heading > button { padding: 5px; color: #92a083; }
.booking-eyebrow { font-size: 9px; letter-spacing: 1.5px; color: #7b9787; }
.booking-heading h3 { margin: 9px 0 0; font-size: 20px; color: #264f40; }
.booking-intro, .booking-help { font-size: 11px; color: #899480; line-height: 1.9; }
.booking-intro { margin: 16px 0; }
.booking-restored { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0 0 14px; padding: 9px 12px; border-radius: 9px; background: #f3f7ee; color: #5b7b53; font-size: 11px; line-height: 1.7; }
.booking-restored button { color: #577e46; text-decoration: underline; font-size: 11px; }
.booking-help { margin: 8px 0 12px; }
.booking-progress { display: flex; align-items: center; gap: 8px; color: #5d855c; font-size: 12px; margin: 15px 0; }
.spinning { animation: booking-spin 1s linear infinite; }
@keyframes booking-spin { to { transform: rotate(360deg); } }
.booking-problem { display: flex; align-items: flex-start; gap: 9px; margin: 16px 0; padding: 13px; border: 1px solid #eadac1; border-radius: 10px; background: #fff6e9; font-size: 12px; color: #987242; line-height: 1.8; overflow-wrap: anywhere; }
.booking-problem > svg { flex-shrink: 0; margin-top: 3px; }
.booking-problem button { display: inline-flex; align-items: center; gap: 8px; padding: 8px 0 0; color: #537f52; }
.booking-secondary { padding: 10px 14px; border: 1px solid #dce5d5; border-radius: 9px; font-size: 12px; }
.booking-form fieldset { min-width: 0; }
.booking-section { margin: 23px 0; min-width: 0; }
.booking-section h4 { display: flex; align-items: center; gap: 8px; color: #315944; font-size: 15px; margin-bottom: 12px; }
.transport-row { padding: 15px; border: 1px solid #dfe7d9; border-radius: 12px; margin: 13px 0; min-width: 0; }
.transport-row header { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 13px; }
.transport-row header strong { color: #466c50; font-size: 13px; }
.transport-row header button { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: #8a947e; padding: 4px; }
.booking-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
.booking-grid label { display: grid; gap: 7px; font-size: 11px; color: #5b755f; min-width: 0; }
.booking-grid input, .booking-grid select { width: 100%; max-width: 100%; min-width: 0; min-height: 42px; padding: 10px; border: 1px solid #dfe5d8; border-radius: 8px; color: #315343; background: #fcfdfb; font-size: 12px; }
.booking-grid input:focus, .booking-grid select:focus { outline: 2px solid #a6cbb3; outline-offset: 1px; }
.booking-grid input::placeholder { color: #9aa38f; font-size: 11px; }
.full-width { grid-column: 1 / -1; }
.buffer-settings { margin-top: 14px; font-size: 11px; color: #7b9075; }
.buffer-settings summary { cursor: pointer; }
.buffer-settings summary svg { vertical-align: middle; margin-left: 3px; }
.booking-add { display: flex; align-items: center; justify-content: center; width: 100%; gap: 7px; padding: 11px; border: 1px dashed #bcd0b4; border-radius: 9px; color: #527b4a; font-size: 12px; }
.booking-confirm { display: flex; align-items: flex-start; gap: 8px; padding: 13px; margin: 17px 0; background: #f0f5eb; border-radius: 10px; font-size: 12px; line-height: 1.8; color: #5b7b53; }
.booking-confirm input { flex-shrink: 0; width: 15px; height: 15px; margin-top: 3px; accent-color: #6e9665; }
.booking-result { border-top: 1px solid #e0e7d8; padding-top: 22px; margin-top: 25px; scroll-margin-top: 24px; }
.booking-result h4 { display: flex; align-items: center; gap: 8px; color: #355f48; font-size: 16px; }
.booking-totals { display: grid; gap: 8px; padding: 16px; background: #f2f6ed; border-radius: 11px; margin: 15px 0; }
.booking-totals > span { font-size: 11px; color: #7c8b73; }
.booking-totals strong { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; font-size: 18px; color: #3c6b4b; }
.booking-notes { padding: 13px; margin: 14px 0; border-radius: 10px; background: #f7f9f2; color: #76886c; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.booking-notes strong, .booking-notes summary { color: #55724a; font-size: 12px; }
.booking-notes summary { cursor: pointer; }
.booking-notes.conflicts { background: #fff1e9; color: #986c48; }
.booking-notes.conflicts strong { color: #94623e; }
.booking-notes ul { margin: 7px 0 0; padding-left: 18px; }
.candidate-days { margin: 15px 0; color: #6a805e; font-size: 12px; }
.candidate-days > summary { cursor: pointer; padding: 8px 0; }
.candidate-days article { padding: 12px 0; border-bottom: 1px solid #eaf0e2; }
.candidate-days article strong { color: #4e6e46; font-size: 12px; }
.candidate-days ol { padding-left: 18px; margin: 10px 0 0; }
.candidate-days li { padding: 5px 0; line-height: 1.7; }
.candidate-days time { display: inline-block; margin-right: 10px; color: #8c987e; font-size: 11px; }
.candidate-days small { display: inline-block; margin-left: 7px; font-size: 10px; color: #7a946c; }
.candidate-days .schedule-change { display: block; margin: 2px 0 0; color: #8e7b55; }
.booking-apply { display: flex; align-items: center; flex-wrap: wrap; gap: 13px; margin: 18px 0 0; }
.booking-apply small { color: #8c9881; font-size: 11px; line-height: 1.8; }
@media (max-width: 600px) { .booking-panel { padding: 17px; } .booking-grid { grid-template-columns: 1fr; } .transport-row { padding: 12px; } .booking-form .primary-button, .booking-apply .primary-button { width: 100%; } .booking-entry > small { flex-basis: 100%; } }
</style>
