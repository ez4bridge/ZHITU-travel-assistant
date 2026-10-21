<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { BookOpen, ExternalLink, LoaderCircle, RefreshCw, Search, X } from 'lucide-vue-next'
import { api } from '../api'
import VisitAdvisory from './VisitAdvisory.vue'
import type { PlaceGuide, QAUsage, Stop, StudyContext } from '../types'

const props = defineProps<{ planId: string; stop?: Stop; stops: Stop[]; date: string; disabled?: boolean; studyContext?: StudyContext | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const saved = ref<Record<string, PlaceGuide>>({}), busy = ref(false), reading = ref(false)
const error = ref(''), lastUsage = ref<QAUsage | null>(null)
let request: AbortController | null = null, readRequest: AbortController | null = null, sequence = 0
const guide = computed(() => props.stop ? saved.value[props.stop.id] : undefined)
const supported = computed(() => props.stop?.kind === 'sight' && props.stop.poi_id && props.stop.location_status === 'verified')
const policy = computed(() => props.stop?.study ? props.studyContext?.policies[props.stop.study.place_id] : undefined)
const policySources = computed(() => props.studyContext?.sources.filter(source => policy.value?.source_ids.includes(source.id)) || [])
const sections = [ { key: 'overview', label: '景点简介与推荐看点' }, { key: 'opening', label: '购票、预约与开放时间' }, { key: 'visit', label: '怎么参观' }, { key: 'tips', label: '实用提醒' } ]
const kindLabel = { official: '官方与机构', public: '公开资料', guide: '游览攻略' }
const usage = computed(() => lastUsage.value || guide.value?.usage)
function timeLabel(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}
function cancel() { sequence++; request?.abort(); request = null; busy.value = false }

async function loadSaved(id: string) {
  readRequest?.abort()
  const controller = new AbortController(); readRequest = controller
  saved.value = {}; reading.value = true
  try {
    const results = await api<PlaceGuide[]>(`/api/plans/${encodeURIComponent(id)}/place-guides`, { signal: controller.signal })
    if (!controller.signal.aborted && props.planId === id) saved.value = Object.fromEntries(results.map(item => [item.stop_id, item]))
  } catch (e) {
    if (!controller.signal.aborted && props.planId === id) error.value = e instanceof Error ? e.message : '读取出行参考失败'
  } finally { if (readRequest === controller) reading.value = false }
}
watch(() => props.planId, id => { void loadSaved(id) }, { immediate: true })

watch(() => [props.planId, props.stop?.id, props.date, props.disabled], () => {
  cancel(); error.value = ''; lastUsage.value = null
})

async function query(refresh = false) {
  if (!supported.value || busy.value || reading.value || props.disabled || !props.stop) return
  const planId = props.planId, stopId = props.stop.id, token = ++sequence
  const controller = new AbortController(); request = controller
  busy.value = true; error.value = ''; lastUsage.value = null
  try {
    const result = await api<PlaceGuide>(`/api/plans/${encodeURIComponent(planId)}/stops/${encodeURIComponent(stopId)}/guide`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh }), signal: controller.signal,
    })
    if (token !== sequence || props.planId !== planId || props.stop?.id !== stopId) return
    lastUsage.value = result.usage
    if (result.status === 'unavailable') error.value = result.message
    else saved.value = { ...saved.value, [stopId]: result }
  } catch (e) {
    if (token === sequence && !controller.signal.aborted) error.value = e instanceof Error ? e.message : '查询失败，请稍后手动重试'
  } finally { if (token === sequence) { busy.value = false; request = null } }
}
onBeforeUnmount(() => { cancel(); readRequest?.abort() })
</script>

<template>
  <section id="place-guide" class="place-guide card" tabindex="-1" aria-label="地点出行参考" :aria-busy="busy">
    <div class="guide-heading"><BookOpen :size="18" /><h3>出行参考</h3><span>按需联网</span></div>
    <div class="guide-places" role="group" aria-label="选择查询攻略的地点"><button v-for="place in stops.filter(item => item.kind === 'sight')" :key="place.id" type="button" :aria-pressed="place.id === stop?.id" :disabled="disabled" @click="emit('select', place.id)">{{ place.name }}</button></div>
    <p class="guide-place">{{ stop?.name || '请先选择一个地点' }}</p>
    <VisitAdvisory v-if="policy" :policy="policy" :sources="policySources" :stop="stop" :date="date" />
    <p v-if="!supported" class="guide-note">请在上方选择已核实地点的景点，再查询简介、看点与出行信息。</p>
    <template v-else>
      <p class="guide-note">{{ date }} · {{ guide ? '已保存攻略，查看不会重新联网' : '选好地点后，点击查询简介、看点、购票与开放时间' }}</p>
      <div class="guide-actions">
        <button v-if="!busy" class="guide-button" :disabled="disabled || reading" @click="query(Boolean(guide))"><RefreshCw v-if="guide" :size="14" /><Search v-else :size="14" />{{ guide ? '更新攻略' : '查询攻略 · 购票/开放时间' }}</button>
        <template v-else><span class="guide-loading" role="status"><LoaderCircle :size="15" class="spin" />正在查找与整理…</span><button class="guide-cancel" @click="cancel"><X :size="13" />取消</button></template>
      </div>
      <p v-if="reading" class="guide-note" role="status">读取已保存的参考…</p>
      <p v-if="error" class="guide-error" role="alert">{{ error }}</p>
      <div v-for="section in sections" :key="section.key" class="guide-section">
        <h4>{{ section.label }}</h4>
        <ul v-if="guide?.items.some(item => item.category === section.key)"><li v-for="(item, index) in guide.items.filter(item => item.category === section.key)" :key="index">{{ item.text }} <a :href="guide.sources.find(source => source.id === item.source_id)?.url" target="_blank" rel="noopener noreferrer" :aria-label="'查看参考来源 ' + item.source_id">[{{ item.source_id }}]</a></li></ul>
        <p v-else class="guide-note guide-empty">{{ busy ? '正在整理…' : reading ? '读取已保存的内容…' : guide ? '暂无可用内容，可更新攻略' : '查询后显示' }}</p>
      </div>
      <template v-if="guide">
        <details class="guide-sources"><summary>来源与依据 · {{ guide.sources.length }}篇</summary>
          <div v-for="source in guide.sources" :key="source.id" class="guide-source"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.id }} · {{ source.title || '查看原始页面' }} <ExternalLink :size="12" /></a><small>{{ kindLabel[source.kind] }} · {{ source.publication_date ? '页面发文日期：' + source.publication_date : '发文日期未确认' }}</small>
            <blockquote v-for="(item, index) in guide.items.filter(item => item.source_id === source.id)" :key="index">{{ item.evidence }}</blockquote>
          </div>
        </details>
        <p class="guide-note guide-time">查询于 {{ timeLabel(guide.queried_at) }}（北京时间）</p>
      </template>
      <details v-if="usage" class="guide-usage"><summary>最近一次查询用量</summary><p>{{ usage.attempted_calls }}次模型调用 · {{ usage.total_tokens == null ? 'token用量未完整回报' : usage.total_tokens.toLocaleString() + ' token' }} · 搜索{{ usage.search_calls || 0 }}次 / 抓取{{ usage.fetch_calls || 0 }}次<span v-if="usage.seconds != null"> · {{ usage.seconds }}秒</span></p><p>用量对应生成这份参考时；读取不产生新调用。搜索与抓取按请求另计。</p></details>
    </template>
    <p class="guide-note guide-footer">供自行参考，不改变行程；出发前请核对官方渠道。</p>
  </section>
</template>

<style scoped>
.place-guide { padding: 19px 17px; min-width: 0; }
.guide-heading { display: flex; align-items: center; gap: 7px; color: #507862; }
.guide-heading h3 { font-size: 14px; margin: 0; }
.guide-heading > span { margin-left: auto; font-size: 10px; color: #849588; }
.guide-places { display: flex; flex-wrap: wrap; gap: 6px; margin: 13px 0; }
.guide-places button { max-width: 100%; padding: 7px 9px; border: 1px solid #e0e8de; border-radius: 8px; font-size: 11px; line-height: 1.6; color: #6c8072; background: #f7faf5; text-align: left; overflow-wrap: anywhere; }
.guide-places button[aria-pressed=true] { color: #176f5d; border-color: #accfbc; background: #eaf4ec; }
.place-guide:focus-visible { outline: 2px solid #8db7a8; outline-offset: 3px; }
.guide-place { font-size: 13px; font-weight: 600; line-height: 1.7; margin: 13px 0 5px; overflow-wrap: anywhere; }
.guide-note { font-size: 11px; color: #819084; line-height: 1.8; margin: 5px 0; }
.guide-actions { display: flex; align-items: center; gap: 12px; margin: 13px 0; }
.guide-button { display: inline-flex; gap: 7px; align-items: center; padding: 9px 13px; border: 1px solid #cddfd4; background: #f0f7f2; color: #357257; border-radius: 8px; font-size: 12px; }
.guide-loading, .guide-cancel { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: #748877; }
.guide-section h4 { font-size: 12px; margin: 14px 0 6px; color: #426955; }
.guide-section ul { padding-left: 17px; margin: 0; }
.guide-section li { font-size: 12px; line-height: 1.9; margin: 6px 0; color: #657b69; overflow-wrap: anywhere; }
.place-guide a { color: #278074; text-decoration: underline; text-underline-offset: 2px; overflow-wrap: anywhere; }
.guide-error { color: #a8784d; font-size: 12px; line-height: 1.8; margin: 12px 0; }
.guide-sources, .guide-usage { font-size: 11px; color: #7f9083; margin-top: 12px; }
summary { cursor: pointer; }
.guide-source { margin-top: 12px; }
.guide-source a { font-size: 11px; line-height: 1.7; }
.guide-source a svg { display: inline; vertical-align: middle; }
.guide-source small { display: block; font-size: 10px; margin: 4px 0; }
.guide-source blockquote { margin: 5px 0 5px 4px; padding-left: 9px; border-left: 2px solid #dce6dc; font-size: 10px; line-height: 1.8; }
.guide-usage p { font-size: 10px; line-height: 1.8; margin-top: 6px; }
.guide-footer { border-top: 1px solid #edf0e8; padding-top: 11px; margin-top: 13px; }
@media(max-width:520px) { .guide-section li { font-size: 13px; } .guide-note { font-size: 12px; } }
</style>
