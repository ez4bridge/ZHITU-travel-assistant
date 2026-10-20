<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { Compass, ArrowUpRight, ArrowRight, MapPin, Users, Wallet,
  Sparkles, Clock3, ChevronRight, Check, CircleCheck, CircleAlert, RefreshCw, X, History, SlidersHorizontal,
  Coffee, BedDouble, Landmark, Send, Route, LoaderCircle, Leaf, ExternalLink } from 'lucide-vue-next'
import CityScene from './components/CityScene.vue'
import RouteMap from './components/RouteMap.vue'
import CityTransfers from './components/CityTransfers.vue'
import DestinationEditor from './components/DestinationEditor.vue'
import RouteChoices from './components/RouteChoices.vue'
import GuideInput from './components/GuideInput.vue'
import StopFacts from './components/StopFacts.vue'
import GuideEvidence from './components/GuideEvidence.vue'
import { api, stream } from './api'
import type { TripRequest, DestinationStay, Plan, Health, HistoryItem, ToolEvent, Stop, ValidationReport, Mode, RouteLeg } from './types'

function localDate() {
  const d = new Date(); d.setDate(d.getDate() + 7)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const form = reactive<Omit<TripRequest, 'days'>>({ origin: '', destination: '', destinations: [{ city: '', preferred_days: 1 }], start_date: localDate(), start_time: '09:00',
  style: 'balanced', budget: 3000, people: 2, mode: 'auto', preferences: '', guides: [], guide_mode: 'prefer', compare_modes: true, confirmed_hotel: '',
  intercity_mode: 'train', include_origin_transport: true, return_to_origin: false })
const revisionScope = ref('all')
const health = ref<Health | null>(null), plan = ref<Plan | null>(null), history = ref<HistoryItem[]>([])
const historyOpen = ref(false), busy = ref(false), progress = ref(''), error = ref(''), tools = ref<ToolEvent[]>([])
const activeDay = ref(0), selectedStopId = ref(''), revision = ref(''), checking = ref(false), showUnknowns = ref(false)
const mapCity = ref('')
const resultSection = ref<HTMLElement>()
let controller: AbortController | null = null
const day = computed(() => plan.value?.itinerary.days[activeDay.value])
const dayRoutes = computed(() => plan.value?.routes.filter(r => r.day_index === activeDay.value) || [])
const dayCities = computed(() => [...new Set((day.value?.stops || []).map(stop => stop.city).filter(Boolean))])
const mapStops = computed(() => (day.value?.stops || []).filter(stop => !mapCity.value || stop.city === mapCity.value))
const mapRoutes = computed(() => dayRoutes.value.filter(route => route.scope !== 'intercity' &&
  mapStops.value.some(stop => stop.id === route.from_id) && mapStops.value.some(stop => stop.id === route.to_id)))
const report = computed(() => plan.value?.validation)
const preferredDays = computed(() => form.destinations.reduce((sum, stay) => sum + Number(stay.preferred_days || 0), 0))
const hasIntercityTravel = computed(() => {
  const cities = form.destinations.map(stay => stay.city.trim().replace(/市$/, '')).filter(Boolean)
  const origin = form.origin.trim().replace(/市$/, '')
  return cities.length > 1 || Boolean(origin && cities[0] && origin !== cities[0] && (form.include_origin_transport || form.return_to_origin))
})
const issues = computed(() => [...(report.value?.errors || []), ...(report.value?.warnings || [])])
const blocked = computed(() => !!report.value?.errors.length)
const validationTitle = computed(() => blocked.value ? '行程尚不可执行，请先处理阻断问题' : issues.value.length ? `${issues.value.length} 项安排需要调整` : '已核对所选路线与时间；开放及预约仍待确认')
const routeAfter = (id: string) => dayRoutes.value.find(r => r.from_id === id)
const totalMinutes = computed(() => Math.ceil(dayRoutes.value.reduce((sum, r) => sum + (r.duration_s || 0), 0) / 60))
const totalDistance = computed(() => (dayRoutes.value.reduce((sum, r) => sum + (r.distance_m || 0), 0) / 1000).toFixed(1))
const categoryNames: Record<string, string> = { ticket: '景点门票', food: '餐饮', hotel: '住宿', transport: '交通（含所选跨城）', other: '其他' }
const costs = computed(() => {
  const values = plan.value?.expense_totals || {}
  return Object.entries(values).map(([category, value]) => ({ category, name: categoryNames[category], value }))
})
const budgetRatio = computed(() => report.value ? Math.min(100, report.value.total_cents / report.value.budget_cents * 100) : 0)
const money = (cents: number) => (cents / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const stopIcon = (stop: Stop) => ({ sight: Landmark, meal: Coffee, hotel: BedDouble, break: Leaf })[stop.kind]
const kindLabel = (kind: string) => ({ sight: '景点', meal: '餐饮', hotel: '住宿', break: '自由活动' })[kind]

async function refreshHistory() { history.value = await api<HistoryItem[]>('/api/plans') }
onMounted(async () => {
  try { health.value = await api<Health>('/api/health'); await refreshHistory() }
  catch { error.value = '无法连接本地服务，请确认启动窗口仍在运行。' }
})

function setDay(index: number) {
  activeDay.value = index
  mapCity.value = ''
  selectedStopId.value = plan.value?.itinerary.days[index]?.stops[0]?.id || ''
}
function selectStop(id: string) {
  selectedStopId.value = id
  if (mapCity.value) mapCity.value = day.value?.stops.find(stop => stop.id === id)?.city || ''
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}
function destinationRows(request: TripRequest): DestinationStay[] {
  return request.destinations?.length
    ? request.destinations.map(stay => ({ city: stay.city, preferred_days: stay.preferred_days == null ? (request.destinations.length === 1 ? request.days : 1) : Number(stay.preferred_days) }))
    : request.destination.split(/\s*(?:→|->|、|，|,|；|;|\n)\s*/).filter(Boolean).map((city, _, cities) => ({ city: city.trim(), preferred_days: cities.length === 1 ? request.days : 1 }))
}
async function openHistory(id: string) {
  if (busy.value) return
  try {
    plan.value = await api<Plan>(`/api/plans/${id}`)
    plan.value.validation = await api<ValidationReport>(`/api/plans/${id}/validate`, { method: 'POST' })
    Object.assign(form, { guides: [], guide_mode: 'prefer', compare_modes: true, confirmed_hotel: '' }, JSON.parse(JSON.stringify(plan.value.request)))
    form.mode = 'auto'
    form.compare_modes = true
    form.destinations = destinationRows(plan.value.request)
    setDay(0); historyOpen.value = false; error.value = ''; tools.value = []
  } catch (e) { error.value = e instanceof Error ? e.message : '读取失败' }
}

async function run(isRevision = false, refresh = false) {
  if (busy.value || checking.value || (isRevision && (!plan.value || !revision.value.trim()))) return
  if (form.destinations.length > 5 || (isRevision && destinationRows(plan.value!.request).length > 5)) {
    error.value = '最多五个目的地。旧六城方案请删减后点击“重新规划一份行程”。'; return
  }
  const destinations = form.destinations.map(stay => ({ city: stay.city.trim(), preferred_days: stay.preferred_days == null ? 1 : Number(stay.preferred_days) }))
  if (!isRevision && destinations.some(stay => !stay.city)) { error.value = '请填写每一站的目的地城市。'; return }
  if (!isRevision && destinations.some(stay => /→|->|、|，|,|；|;|\n/.test(stay.city))) {
    error.value = '每一行只填写一个城市，请用“添加目的地”增加下一站。'; return
  }
  if (!isRevision && destinations.some((stay, index) => index > 0 && stay.city.replace(/市$/, '') === destinations[index - 1]!.city.replace(/市$/, ''))) {
    error.value = '相邻两站不能是同一个城市，请合并停留时间或调整顺序。'; return
  }
  if (!isRevision && Math.ceil(preferredDays.value) > 10) {
    error.value = '各站游玩天数合计最多 10 天，请调整各城市时长。'; return
  }
  busy.value = true; error.value = ''; tools.value = []; progress.value = '准备开始规划…'
  controller = new AbortController()
  try {
    await nextTick(); resultSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    const trip = { ...form, mode: 'auto', compare_modes: true, destination: destinations.map(stay => stay.city).join('→'), destinations,
      days: undefined, people: Number(form.people), budget: Number(form.budget),
      guides: (form.guides || []).map((source, index) => ({ title: `参考攻略 ${index + 1}`, text: source.text })),
      include_origin_transport: Boolean(form.origin.trim() && form.include_origin_transport),
      return_to_origin: Boolean(form.origin.trim() && form.return_to_origin) }
    await stream(refresh ? `/api/plans/${plan.value!.id}/refresh` : isRevision ? `/api/plans/${plan.value!.id}/revise` : '/api/plans/generate',
      refresh ? {} : isRevision ? { instruction: revision.value, trip, day_index: revisionScope.value === 'day' ? activeDay.value : null } : trip,
      controller.signal, (kind, data) => {
        if (kind === 'status') progress.value = (data as { message: string }).message
        if (kind === 'tool') tools.value.push(data as ToolEvent)
        if (kind === 'result') { plan.value = data as Plan; setDay(0); revision.value = ''; progress.value = '行程已保存，请查看路线与待核实信息' }
      })
    await refreshHistory()
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') progress.value = '本次规划已取消，已有方案仍然保留'
    else error.value = e instanceof Error ? e.message : '规划失败，请重试'
  } finally { busy.value = false; controller = null }
}
async function chooseRoute(route: RouteLeg, mode: Mode) {
  if (!plan.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    plan.value = await api<Plan>(`/api/plans/${plan.value.id}/route`, { method: 'POST', body: JSON.stringify({ day_index: route.day_index, from_id: route.from_id, to_id: route.to_id, mode, expected_routing_revision: plan.value.routing_revision || 0 }) })
    progress.value = '交通选择已保存到当前行程，未新增版本；时间和费用已重算'
    await refreshHistory()
  } catch (e) { error.value = e instanceof Error ? e.message : '更换路线失败' }
  finally { busy.value = false }
}
async function deleteHistory() {
  if (!plan.value || busy.value) return
  try {
    await api(`/api/plans/${plan.value.id}`, { method: 'DELETE' })
    plan.value = null; await refreshHistory(); progress.value = '已删除所选版本，其他历史版本保留'
  } catch (e) { error.value = e instanceof Error ? e.message : '删除失败' }
}
async function validateAgain() {
  if (!plan.value || checking.value || busy.value) return
  checking.value = true
  try { plan.value.validation = await api<ValidationReport>(`/api/plans/${plan.value.id}/validate`, { method: 'POST' }) }
  catch (e) { error.value = e instanceof Error ? e.message : '校验失败' }
  finally { checking.value = false }
}
function reviseProblems() {
  revision.value = '请修正当前校验发现的问题，保留我的偏好。重新安排时间以满足真实路线时长，避免重复景点，并让全员估算费用不超过预算。尽量满足各城期望游玩时长；无法满足时明确说明原因。'
  run(true)
}
function followGuides() {
  form.guide_mode = 'prefer'
  revisionScope.value = 'all'
  revision.value = '请优先按我粘贴的攻略地点、游玩顺序和玩法重新规划；保留日期、城市与预算等约束，逐条列出原文摘录、对应活动，说明调整或未采纳原因。'
  run(true)
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="漫游首页"><span class="brand-symbol"><Compass :size="24" /></span><strong>漫游<span>ROAM</span></strong></a>
      <nav><a href="#planner" class="nav-active">规划旅程</a><button @click="historyOpen = true"><History :size="16" /> 我的行程 <span v-if="history.length" class="count">{{ history.length }}</span></button></nav>
      <div class="topbar-note"><span class="live-dot" :class="{ offline: !health?.capabilities.planning }"></span>{{ health ? (health.capabilities.planning ? '准备好出发了' : '检查服务配置') : '正在连接服务…' }}</div>
    </header>

    <section v-if="!plan" class="hero">
      <div class="hero-copy">
        <span class="eyebrow"><Sparkles :size="14" /> YOUR NEXT LITTLE ADVENTURE</span>
        <h1>好风景，<br />也要<span>好行程。</span></h1>
        <p>把期待告诉我们。让 AI 整理灵感，<br class="desktop-break" />让真实路线，把每一站串成你的旅程。</p>
        <a href="#planner" class="hero-link">开始我的全国旅程 <ArrowUpRight :size="20" /></a>
        <div class="hero-features"><span><Route :size="15" /> 真实地图路线</span><span><Wallet :size="15" /> 全员预算核对</span><span><SlidersHorizontal :size="15" /> 随心调整行程</span></div>
      </div>
      <CityScene />
    </section>

    <main id="planner" class="planner">
      <div class="section-heading"><div><span class="eyebrow">MAKE IT YOURS</span><h2>{{ plan ? '你的旅程，有了轮廓。' : '下一站，你想去哪里？' }}</h2></div><span class="section-meta">全国多城市旅行规划 <span>01 — 03</span></span></div>
      <div class="planner-grid">
        <aside class="request-panel card">
          <div class="panel-heading"><div class="icon-tile"><SlidersHorizontal :size="19" /></div><div><h3>旅行偏好</h3><p>从几个小小的期待开始</p></div></div>
          <form @submit.prevent="run()">
            <fieldset :disabled="busy">
              <label class="field"><span>出发城市 <small>可选</small></span><div class="input-wrap"><Compass :size="17" /><input v-model="form.origin" maxlength="40" placeholder="例如：北京" /></div></label>
              <DestinationEditor v-model="form.destinations" :disabled="busy" />
              <p v-if="Math.ceil(preferredDays) > 10" class="stay-budget-note" role="status">各站游玩天数合计最多 10 天，请减少部分城市的时长。</p>
              <div v-if="form.origin.trim()" class="journey-options"><label><input v-model="form.include_origin_transport" type="checkbox" :disabled="form.return_to_origin" /> 安排从出发地到首城的交通</label><label><input v-model="form.return_to_origin" type="checkbox" @change="form.include_origin_transport = form.include_origin_transport || form.return_to_origin" /> 最后返回出发地</label></div>
              <div class="field-row"><label class="field"><span>出行日期</span><div class="input-wrap"><input v-model="form.start_date" type="date" required aria-label="出行日期" /></div></label><label class="field time-field"><span>首日开始</span><div class="input-wrap"><input v-model="form.start_time" type="time" required aria-label="首日开始时间" /></div></label></div>
              <label class="field"><span>同行人数</span><div class="input-wrap"><Users :size="16" /><input v-model.number="form.people" type="number" min="1" max="50" required /><span class="input-unit">人</span></div></label>
              <label class="field"><span>全员总预算 <small>全程含所选跨城交通</small></span><div class="input-wrap"><Wallet :size="17" /><input v-model.number="form.budget" type="number" min="1" max="10000000" step="0.01" required /><span class="input-unit">元</span></div></label>
              <div class="field"><span>旅行节奏</span><div class="segmented"><button type="button" :class="{ selected: form.style === 'relaxed' }" @click="form.style = 'relaxed'">慢慢逛</button><button type="button" :class="{ selected: form.style === 'balanced' }" @click="form.style = 'balanced'">刚刚好</button><button type="button" :class="{ selected: form.style === 'compact' }" @click="form.style = 'compact'">多看些</button></div></div>
              <label v-if="hasIntercityTravel" class="field"><span>跨城交通偏好</span><div class="input-wrap"><Route :size="17" /><select v-model="form.intercity_mode" aria-label="跨城交通偏好"><option value="train">铁路（高铁／火车）</option><option value="flight">飞机</option><option value="driving">自驾</option><option value="bus">长途汽车</option></select></div></label>
              <p class="form-note">市内交通自动比较地图通行时间，优先显示最快可用方式；结果中可逐段更换。</p>
              <label class="field"><span>首站城市已确认酒店（可选）</span><div class="input-wrap"><BedDouble :size="16" /><input v-model="form.confirmed_hotel" maxlength="120" placeholder="留空只推荐住宿片区" /></div></label>
              <label class="field preferences-field"><span>还有什么小期待？</span><textarea v-model="form.preferences" rows="3" maxlength="2000" placeholder="想看建筑、尝本地美食，或带孩子轻松逛逛…"></textarea></label>
              <GuideInput v-model="form.guides" v-model:mode="form.guide_mode" />
              <button class="primary-button generate-button" type="submit" :disabled="!health?.capabilities.planning"><LoaderCircle v-if="busy" :size="18" class="spin" /><Sparkles v-else :size="18" />{{ busy ? '正在规划旅程' : plan ? '重新规划一份行程' : '生成我的专属行程' }}<ArrowRight v-if="!busy" :size="17" /></button>
            </fieldset>
          </form>
          <p class="form-note">预算包含全员游玩、餐饮、住宿和所选跨城交通。铁路查询成功后显示实际车次与参考票价，未查询的交通保留估算。</p>
          <div v-if="health?.missing.length" class="config-notice"><CircleAlert :size="16" /><span>待配置：{{ health.missing.join('、') }}</span></div>
        </aside>

        <section ref="resultSection" class="workspace" aria-live="polite">
          <div v-if="error" class="error-banner" role="alert"><CircleAlert :size="19" /><div><strong>这次没能顺利完成</strong><p>{{ error }}</p></div><button aria-label="关闭错误提示" @click="error = ''"><X :size="17" /></button></div>
          <div v-if="busy" class="progress-card card"><div class="progress-icon"><Compass :size="26" class="spin-slow" /></div><div><span class="eyebrow">A LITTLE PLANNING, A LOT TO DISCOVER</span><h3>{{ progress }}</h3><p>正在结合地点、路线和你的旅行偏好。通常需要几十秒。</p></div><button class="text-button" @click="controller?.abort()">取消</button><div class="progress-line"></div></div>

          <template v-if="plan && day">
            <div class="plan-heading card"><div><span class="eyebrow"><MapPin :size="13" /> {{ plan.request.destination }} · {{ plan.request.days }} 天 · {{ plan.request.people }} 人</span><h2>{{ plan.itinerary.title }}</h2><p>{{ plan.itinerary.summary }}</p></div><span class="revision-badge">版本 {{ plan.revision }}</span></div>
            <CityTransfers :transfers="plan.itinerary.transfers" />
            <div class="day-tabs" role="tablist" aria-label="选择行程日期"><button v-for="(d, i) in plan.itinerary.days" :key="d.date" role="tab" :aria-selected="activeDay === i" :class="{ active: activeDay === i }" @click="setDay(i)"><span>DAY {{ String(i + 1).padStart(2, '0') }}</span><strong>{{ d.date.slice(5).replace('-', ' / ') }}</strong></button></div>
            <div class="journey-layout">
              <div class="day-itinerary card"><div class="day-title"><span class="icon-tile"><Route :size="19" /></span><div><h3>{{ day.title }}</h3><p>{{ dayCities.join(' → ') || '跨城移动日' }} · {{ day.stops.length }} 个活动 · 逐段交通以时间轴选择为准</p></div></div>
                <p v-if="!day.stops.length" class="form-note">今天没有游玩活动，请查看上方跨城交通安排。</p>
                <div class="timeline">
                  <template v-for="(stop, i) in day.stops" :key="stop.id">
                    <button :id="stop.id" class="stop-card" :class="{ active: selectedStopId === stop.id }" @click="selectStop(stop.id)">
                      <span class="stop-index">{{ String(i + 1).padStart(2, '0') }}</span><div class="stop-body"><div class="stop-meta"><span>{{ stop.start }} — {{ stop.end }}</span><span class="kind-tag"><component :is="stopIcon(stop)" :size="12" />{{ kindLabel(stop.kind) }}</span></div><h4><small v-if="stop.city" class="stop-city">{{ stop.city }}</small>{{ stop.name }}</h4><p>{{ stop.description }}</p><span class="poi-note" :class="{ unknown: stop.location_status !== 'verified' }"><MapPin :size="12" />{{ stop.location_status === 'verified' ? (stop.matched_name || stop.name) : stop.kind === 'break' ? '自由活动' : '地点待核对' }}</span><small v-if="selectedStopId === stop.id">{{ stop.location_note }}</small><span v-if="stop.address && selectedStopId === stop.id" class="stop-address">{{ stop.address }}</span></div><ChevronRight :size="15" class="stop-arrow" />
                    </button>
                    <StopFacts :stop="stop" :expenses="day.expenses.filter(cost => cost.stop_id === stop.id && cost.category !== 'transport')" />
                    <p v-if="i < day.stops.length - 1 && routeAfter(stop.id)?.scope === 'intercity'" class="transfer">跨城移动 · 见上方交通安排</p>
                    <RouteChoices v-else-if="i < day.stops.length - 1 && routeAfter(stop.id)" :route="routeAfter(stop.id)!" :disabled="busy || checking" @select="chooseRoute(routeAfter(stop.id)!, $event)" />
                    <p v-else-if="i < day.stops.length - 1" class="subtle-note">该段路线尚未查询，请刷新地点与路线。</p>
                  </template>
                </div>
                <div v-if="day.meal_note" class="accommodation meal-advice"><Coffee :size="17" /><div><strong>用餐提醒</strong><p>{{ day.meal_note }}</p></div></div>
                <div v-if="day.accommodation" class="accommodation"><BedDouble :size="17" /><div><strong>今晚住哪里</strong><p>{{ day.accommodation }}</p><small>住宿建议与价格需要自行核实</small></div></div>
              </div>
              <div class="map-panel card"><div class="map-heading"><div><MapPin :size="17" /><strong>把行程放到地图上</strong></div><span>点击标记查看地点</span></div><div v-if="dayCities.length > 1" class="map-city-tabs" role="group" aria-label="选择地图城市"><button :class="{ active: !mapCity }" @click="mapCity = ''">当天全部</button><button v-for="city in dayCities" :key="city" :class="{ active: mapCity === city }" @click="mapCity = city">{{ city }}</button></div><RouteMap :stops="mapStops" :routes="mapRoutes" :selected-id="selectedStopId" @select="selectStop" /><div class="map-stats"><div><Route :size="16" /><span>{{ totalDistance }} <small>km</small></span><label>当天市内路线总长</label></div><div><Clock3 :size="16" /><span>{{ totalMinutes }} <small>分钟</small></span><label>当天市内通行时长</label></div></div><p class="map-footnote">地图只绘制高德市内道路路线，跨城交通见上方安排；缺失路线不会用直线代替。</p></div>
            </div>

            <div class="insights-grid">
              <div class="budget-card card"><div class="mini-heading"><Wallet :size="18" /><h3>旅行预算</h3><span>全员 · 全程估算</span></div><div class="budget-amount"><strong>¥{{ money(report!.total_cents) }}</strong><span>/ ¥{{ money(report!.budget_cents) }}</span></div><div class="budget-bar"><span :style="{ width: budgetRatio + '%' }" :class="{ over: report!.total_cents > report!.budget_cents }"></span></div><div class="cost-list"><div v-for="cost in costs" :key="cost.category"><span>{{ cost.name }}</span><strong>¥{{ money(cost.value) }}</strong></div></div><p class="subtle-note">所有费用均为估算，不代表实时票价或酒店报价。</p></div>
              <div class="validation-card card"><div class="mini-heading"><CircleCheck :size="18" /><h3>出发前，检查一下</h3><button class="icon-button" :disabled="checking || busy" aria-label="重新校验方案" @click="validateAgain"><RefreshCw :size="15" :class="{ spin: checking }" /></button></div><div class="validation-status" :class="{ 'has-issues': issues.length }"><component :is="issues.length ? CircleAlert : CircleCheck" :size="21" /><div><strong>{{ validationTitle }}</strong><p>{{ report!.unknowns.length ? `还有 ${report!.unknowns.length} 项信息需要核实` : '检查范围内未发现其他问题' }}</p></div></div><ul v-if="issues.length" class="issues-list"><li v-for="(issue, i) in issues" :key="i"><span v-if="issue.day_index !== null">第 {{ issue.day_index + 1 }} 天：</span>{{ issue.message }}</li></ul><button v-if="issues.length" class="secondary-button" :disabled="busy" @click="reviseProblems"><Sparkles :size="15" /> 请 AI 调整这些问题</button><button class="unknown-toggle" @click="showUnknowns = !showUnknowns">{{ showUnknowns ? '收起' : '查看' }}待确认信息 <ChevronRight :size="14" :class="{ rotated: showUnknowns }" /></button><ul v-if="showUnknowns" class="unknown-list"><li v-for="(item, i) in report!.unknowns" :key="i">{{ item.day_index !== null ? `第 ${item.day_index + 1} 天：` : '' }}{{ item.message }}</li></ul></div>
            </div>
            <div class="plan-actions card"><button class="secondary-button" :disabled="busy || checking" @click="run(false, true)">更新路线与顺路餐饮</button><button v-if="form.guides?.length" class="text-button" :disabled="busy || checking" @click="followGuides">按攻略优先重新规划</button><small>活动变更才新增方案；仅更新路线或切换交通直接保存到当前行程。</small><button class="text-button" :disabled="busy || checking" @click="deleteHistory">删除当前历史版本</button></div>
            <GuideEvidence :guides="plan.request.guides || []" :decisions="plan.itinerary.guide_decisions || []" :stops="plan.itinerary.days.flatMap(d => d.stops)" />
            <div v-if="plan.adjustments?.length" class="travel-tips"><Clock3 :size="17" /><div><strong>时间调整记录</strong><p v-for="item in plan.adjustments" :key="item">{{ item }}</p></div></div>
            <div class="revise-card card"><div class="mini-heading"><Sparkles :size="19" /><h3>让它更像你的旅行</h3><span>每次调整保留旧版本</span></div><p>修改会应用左侧当前需求；仅调整当天时保留其他天的活动内容。</p><label class="field"><span>调整范围</span><select v-model="revisionScope" :disabled="busy"><option value="all">整个行程</option><option value="day">仅当前第 {{ activeDay + 1 }} 天</option></select></label><form class="revision-input" @submit.prevent="run(true)"><input v-model="revision" maxlength="2000" :disabled="busy" placeholder="例如：第二天少安排一个景点，多留些休息时间" aria-label="行程修改要求" /><button type="submit" :disabled="busy || !revision.trim()" aria-label="提交行程修改"><Send :size="18" /></button></form></div>
            <div v-if="plan.itinerary.guide_insights?.length" class="travel-tips"><Landmark :size="17" /><div><strong>攻略参考摘要（经验信息，未经官方核实）</strong><article v-for="(insight, i) in plan.itinerary.guide_insights" :key="i"><p>{{ insight.text }}</p><a v-if="plan.request.guides?.[insight.source_index]?.url" :href="plan.request.guides[insight.source_index]!.url" target="_blank" rel="noopener noreferrer">{{ plan.request.guides[insight.source_index]!.title }}</a><small v-else>{{ plan.request.guides?.[insight.source_index]?.title }}</small></article></div></div>
            <div v-if="plan.itinerary.tips.length" class="travel-tips"><Leaf :size="17" /><div><strong>一点旅行小提醒</strong><p v-for="tip in plan.itinerary.tips" :key="tip">{{ tip }}</p></div></div>
          </template>

          <div v-else-if="!busy" class="empty-workspace card">
            <div class="empty-heading"><span class="eyebrow">A JOURNEY WAITING TO HAPPEN</span><span class="outline-tag"><Compass :size="14" /> 专属于你的行程</span></div>
            <div class="empty-art"><div class="art-ring ring-one"></div><div class="art-ring ring-two"></div><div class="art-compass"><Compass :size="58" stroke-width="1.1" /></div><span class="art-chip chip-one"><Coffee :size="16" /> 一杯本地咖啡</span><span class="art-chip chip-two"><Landmark :size="16" /> 一段城市故事</span><span class="art-chip chip-three"><Leaf :size="16" /> 一点留白的时间</span></div>
            <h3>旅程，从你的期待开始。</h3><p>填写左侧的旅行偏好，我们会为你整理每日行程，<br />计算真实路线，并检查时间与预算是否合适。</p>
            <div class="empty-steps"><div><span>01</span><strong>说说你的期待</strong><small>目的地、时间与偏好</small></div><ArrowRight :size="16" /><div><span>02</span><strong>看见旅行轮廓</strong><small>每日行程与真实路线</small></div><ArrowRight :size="16" /><div><span>03</span><strong>调整成你的节奏</strong><small>校验问题，一次次完善</small></div></div>
            <div class="empty-bottom"><CircleCheck :size="14" /> 不需要下载大模型或 PDF 权重，就能开始规划。</div>
          </div>
          <details v-if="tools.length" class="tool-log card"><summary><Route :size="16" /> 查看本次查询记录 <span>{{ tools.length }} 次工具调用</span></summary><div class="tool-log-body"><article v-for="(tool, i) in tools" :key="i"><strong>{{ tool.name }}</strong><code>{{ JSON.stringify(tool.arguments) }}</code><p>{{ tool.summary }}</p></article></div></details>
          <p v-if="progress && !busy && !error" class="completion-note"><Check :size="14" /> {{ progress }}</p>
        </section>
      </div>
    </main>
    <footer><a class="footer-brand" href="#"><Compass :size="17" /> 漫游 · ROAM</a><p>认真规划，自在出发。</p><a href="https://lbs.amap.com/" target="_blank" rel="noopener noreferrer">地图与路线由高德提供 <ExternalLink :size="12" /></a></footer>

    <div v-if="historyOpen" class="modal-backdrop" @click.self="historyOpen = false"><section class="history-modal card" role="dialog" aria-modal="true" aria-labelledby="history-title"><div class="mini-heading"><History :size="20" /><h3 id="history-title">我的行程</h3><button class="icon-button" aria-label="关闭行程历史" @click="historyOpen = false"><X :size="20" /></button></div><p v-if="!history.length" class="history-empty">还没有保存的行程。完成第一次规划后，会自动出现在这里。</p><button v-for="item in history" :key="item.id" class="history-item" :disabled="busy" @click="openHistory(item.id)"><span class="icon-tile"><MapPin :size="20" /></span><div><strong>{{ item.title }}</strong><span>{{ item.destination }} · {{ item.start_date }} · 版本 {{ item.revision }}</span></div><ChevronRight :size="18" /></button></section></div>
  </div>
</template>
