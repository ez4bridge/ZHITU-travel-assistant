<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowRight, BedDouble, Check, ChevronDown, CircleAlert, ClipboardCheck, ExternalLink, LoaderCircle, RefreshCw, Ticket } from 'lucide-vue-next'
import { getPreparation } from '../preparationApi'
import { hotelPreparationLabels, preparationModeLabels, preparationState, preparationTitles, safePreparationLink, transportPreparationLabels } from '../preparationPresentation'
import type { PreparationSummary } from '../preparationTypes'
import type { Plan } from '../types'

const props = defineProps<{ plan: Plan; disabled?: boolean }>()
const emit = defineEmits<{ edit: []; jump: [target: { day_index?: number | null; stop_id?: string | null; target?: string }] }>()
const summary = ref<PreparationSummary | null>(null), loading = ref(false), error = ref('')
const collapsed = ref(true)
let controller: AbortController | null = null, sequence = 0
const state = computed(() => summary.value ? preparationState(summary.value) : null)
const title = computed(() => state.value ? preparationTitles[state.value] : loading.value ? '正在核对出行准备信息' : '出行准备状态待核对')
const issues = computed(() => summary.value?.issues || [])
const blocks = computed(() => issues.value.filter(item => item.severity === 'block').length)
const snapshot = computed(() => JSON.stringify([props.plan.id, props.plan.routing_revision, props.plan.validation]))
function cancel() { sequence++; controller?.abort(); controller = null }
async function refresh() {
  cancel(); summary.value = null; error.value = ''; loading.value = true
  const current = sequence, planId = props.plan.id
  controller = new AbortController()
  try {
    const result = await getPreparation(planId, controller.signal)
    if (sequence === current && props.plan.id === planId) summary.value = result
  } catch (reason) {
    if (sequence === current && !(reason instanceof DOMException && reason.name === 'AbortError')) error.value = reason instanceof Error ? reason.message : '暂时无法核对准备信息，请稍后重试。'
  } finally { if (sequence === current) { loading.value = false; controller = null } }
}
watch(snapshot, refresh, { flush: 'sync' })
onMounted(refresh)
onBeforeUnmount(cancel)
const money = (amount: string) => Number.isFinite(Number(amount)) ? Number(amount).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '待核实'
</script>

<template>
  <section class="preparation-card card" :class="state || 'unverified'" aria-labelledby="preparation-heading">
    <header class="preparation-heading"><div class="preparation-title"><span class="preparation-icon"><Check v-if="state === 'ready'" :size="19" /><ClipboardCheck v-else :size="19" /></span><div><span class="preparation-eyebrow">出行准备</span><h3 id="preparation-heading">{{ title }}</h3></div></div><div class="preparation-tools"><button type="button" class="preparation-toggle" :aria-expanded="!collapsed" aria-label="收起或展开出行准备" @click="collapsed = !collapsed"><ChevronDown :size="17" :class="{ flipped: collapsed }" /></button><button type="button" class="preparation-refresh" :disabled="loading || disabled" aria-label="重新核对出行准备状态" @click="refresh"><LoaderCircle v-if="loading" class="spin" :size="17" /><RefreshCw v-else :size="17" /></button></div></header>
    <div v-show="!collapsed" class="preparation-body">
    <p v-if="loading" class="preparation-summary" role="status">正在读取当前版本的酒店、交通与检查结果；不会查询外网或改动行程。</p>
    <p v-else-if="error" class="preparation-error" role="alert"><CircleAlert :size="16" />{{ error }} 当前行程仍保留，尚不能确认已准备好。</p>
    <template v-else-if="summary">
      <p class="preparation-summary">{{ summary.summary }}</p>
      <div v-if="state === 'preview'" class="preparation-status" role="status"><CircleAlert :size="16" /><span>还有 {{ summary.missing_count }} 项必要信息待补齐。现在展示的是规划预览，填写酒店和选定交通后需重新核对位置、时间与费用。</span></div>
      <div v-else-if="state === 'needs_review'" class="preparation-status" role="status"><CircleAlert :size="16" /><span>酒店与交通信息已提供，当前检查仍有问题；补录完成不代表路线、时间或预算已经可行。</span></div>
      <div v-else class="preparation-status" role="status"><Check :size="16" /><span>已提供必要信息并通过本次检查；订单、票价、房态、开放与预约仍需向服务方复核。</span></div>
      <div v-if="issues.length" class="preparation-pointer">
        <CircleAlert :size="16" />
        <span>检查发现待处理或待确认的安排<span v-if="blocks">（其中 {{ blocks }} 项会阻断执行）</span>；逐条明细见下方「出发前，检查一下」，同一提醒不在两处重复。</span>
        <button type="button" @click="emit('jump', { target: 'validation' })">查看明细 <ArrowRight :size="13" /></button>
      </div>
      <div class="preparation-grid">
        <section class="preparation-group"><h4><BedDouble :size="17" /> 先确定住在哪里</h4><p v-if="!summary.hotels.length" class="preparation-note">本行程没有需要补充的过夜住宿。</p>
          <article v-for="hotel in summary.hotels" :key="hotel.city" class="preparation-item"><header><strong>{{ hotel.city }}</strong><span class="preparation-tag" :class="hotel.status">{{ hotelPreparationLabels[hotel.status] }}</span></header><p class="preparation-dates">住宿晚间：{{ hotel.nights.join('、') || '按实际住宿日期核对' }}</p><p v-if="hotel.names.length" class="preparation-known">{{ hotel.names.join('、') }}</p>
            <div v-for="(suggestion, index) in hotel.suggestions" :key="index" class="preparation-suggestion"><strong>{{ suggestion.label }}</strong><small>{{ suggestion.source_status === 'map_reference' ? '已有地图位置参考' : '当前行程的住宿片区建议' }} · 未查实时房价、评分或空房</small><p>{{ suggestion.basis }}</p><div class="preparation-links"><a v-if="safePreparationLink(suggestion.source_url)" :href="safePreparationLink(suggestion.source_url)" target="_blank" rel="noopener noreferrer">查看位置依据 <ExternalLink :size="12" /></a><a v-if="safePreparationLink(suggestion.query_url)" :href="safePreparationLink(suggestion.query_url)" target="_blank" rel="noopener noreferrer">外部查询住宿 <ExternalLink :size="12" /></a></div></div>
            <p v-for="note in hotel.notes" :key="note" class="preparation-note">{{ note }}</p><a v-if="safePreparationLink(hotel.query_url)" class="preparation-query" :href="safePreparationLink(hotel.query_url)" target="_blank" rel="noopener noreferrer">查看该城市住宿 <ExternalLink :size="12" /></a>
          </article>
        </section>
        <section class="preparation-group"><h4><Ticket :size="17" /> 再核对跨城交通</h4><p v-if="!summary.transports.length" class="preparation-note">本行程没有需要确认的跨城交通段。</p>
          <article v-for="transport in summary.transports" :key="transport.index" class="preparation-item"><header><strong>{{ transport.from_city }} → {{ transport.to_city }}</strong><span class="preparation-tag" :class="transport.status">{{ transportPreparationLabels[transport.status] }}</span></header><p class="preparation-dates">{{ transport.travel_date }} · {{ preparationModeLabels[transport.mode] || transport.mode }}<span v-if="transport.number"> · {{ transport.number }}</span></p><p v-if="transport.departure_time || transport.arrival_time" class="preparation-known">{{ transport.departure_time || '出发时间待核实' }} → {{ transport.arrival_time || '到达时间待核实' }}</p><p class="preparation-note">全员该段参考费用：{{ transport.reference_total_yuan == null ? '待核实' : `¥${money(transport.reference_total_yuan)}` }}；不代表锁价或已付款。</p>
            <div v-for="(option, index) in transport.options" :key="index" class="preparation-suggestion"><strong>{{ option.label }}</strong><small>{{ option.source_status === 'queried' ? '已有查询参考，未占座／锁价' : option.source_status === 'estimate' ? '规划估算，待查询核实' : '外部查询入口，未取得实时票务' }}</small><p>{{ option.summary }}</p><a v-if="safePreparationLink(option.query_url)" class="preparation-query" :href="safePreparationLink(option.query_url)" target="_blank" rel="noopener noreferrer">查询与购票 <ExternalLink :size="12" /></a></div><p v-for="note in transport.notes" :key="note" class="preparation-note">{{ note }}</p>
          </article>
        </section>
      </div>
    </template>
    <div class="preparation-actions"><button type="button" class="secondary-button" :disabled="disabled" @click="$emit('edit')"><BedDouble :size="16" /> {{ state === 'preview' ? '补充酒店与选定交通' : '补充／修改酒店与交通' }} <ArrowRight :size="15" /></button><small>在原有补录窗口填写；先预览影响，确认后保存新版本。不会替你预订、改签或取消。</small></div>
    </div>
  </section>
</template>

<style scoped>
.preparation-card { padding:22px; margin:0 0 20px; min-width:0; border-color:#d7e3d6; scroll-margin-top:24px; }
.preparation-card.preview,.preparation-card.needs_review { border-color:#e7d7bc; }
.preparation-heading,.preparation-title { display:flex; align-items:flex-start; gap:12px; }
.preparation-heading { justify-content:space-between; }
.preparation-title { min-width:0; }
.preparation-icon { display:grid; place-items:center; width:38px; height:38px; flex-shrink:0; border-radius:12px; color:#4b8066; background:#eef5ec; }
.preparation-eyebrow { font-size:16px; font-weight:600; color:#315843; }
h3 { margin:5px 0 0; color:#71856f; font-size:14px; font-weight:400; line-height:1.7; overflow-wrap:anywhere; }
.preparation-refresh { flex-shrink:0; padding:7px; color:#79916d; }
.preparation-tools { display:flex; align-items:center; gap:4px; flex-shrink:0; }
.preparation-toggle { flex-shrink:0; padding:7px; color:#79916d; }
.preparation-toggle svg { transition:transform .2s ease; }
.preparation-toggle .flipped { transform:rotate(-90deg); }
.preparation-summary { margin:14px 0; font-size:12px; line-height:1.8; color:#72816c; }
.preparation-status,.preparation-error { display:flex; align-items:flex-start; gap:8px; padding:12px; border-radius:10px; font-size:12px; line-height:1.8; color:#896b43; background:#faf5e9; overflow-wrap:anywhere; }
.preparation-status svg,.preparation-error svg { margin-top:3px; flex-shrink:0; }
.ready .preparation-status { color:#47725a; background:#f0f7ed; }
.preparation-pointer { display:flex; align-items:flex-start; flex-wrap:wrap; gap:9px; margin:14px 0; padding:12px; border:1px solid #e7d7bc; border-radius:10px; background:#faf5e9; color:#8a6c42; font-size:12px; line-height:1.8; }
.preparation-pointer svg { margin-top:3px; flex-shrink:0; }
.preparation-pointer span { flex:1 1 260px; min-width:0; }
.preparation-pointer button { display:inline-flex; align-items:center; gap:4px; color:#7a5a2f; font-size:11px; text-decoration:underline; white-space:nowrap; }
.preparation-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; margin:20px 0 0; }
.preparation-group { min-width:0; }
h4 { display:flex; align-items:center; gap:7px; color:#426950; font-size:13px; margin:0 0 12px; }
.preparation-item { padding:13px; border:1px solid #e2e9db; border-radius:11px; margin-top:11px; min-width:0; overflow-wrap:anywhere; }
.preparation-item header { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:7px; }
.preparation-item header strong,.preparation-suggestion>strong { font-size:12px; color:#426b51; }
.preparation-tag { font-size:10px; line-height:1.7; padding:3px 7px; border-radius:6px; color:#8b754d; background:#f5f0e4; }
.preparation-tag.located,.preparation-tag.user_provided { color:#65805f; background:#f0f5e9; }
.preparation-dates { color:#8c957f; font-size:10px; line-height:1.8; margin:8px 0; }
.preparation-known { color:#54745a; font-size:12px; line-height:1.7; margin:8px 0; }
.preparation-suggestion { background:#f7f9f3; padding:11px; margin:10px 0; border-radius:8px; }
.preparation-suggestion small { display:block; font-size:10px; line-height:1.8; color:#8c967e; margin-top:5px; }
.preparation-suggestion p,.preparation-note { color:#7c8b74; font-size:11px; line-height:1.8; margin:7px 0; }
.preparation-links { display:flex; flex-wrap:wrap; gap:12px; }
.preparation-links a,.preparation-query { display:inline-flex; align-items:center; gap:5px; color:#4d7d60; font-size:11px; text-decoration:underline; line-height:1.8; }
.preparation-unknowns { margin:17px 0 0; font-size:11px; line-height:1.8; color:#7b8c6f; }
.preparation-actions { display:flex; align-items:center; flex-wrap:wrap; gap:12px; margin:20px 0 0; }
.preparation-actions button { white-space:normal; }
.preparation-actions small { flex:1 1 240px; font-size:11px; color:#8c967f; line-height:1.8; }
@media(max-width:650px) { .preparation-card { padding:17px; } .preparation-grid { grid-template-columns:1fr; gap:15px; } h3 { font-size:15px; } .preparation-actions button { width:100%; justify-content:center; } }
</style>
