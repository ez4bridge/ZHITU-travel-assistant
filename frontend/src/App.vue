<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Compass, ArrowUpRight, ArrowRight, MapPin, Users, Wallet, Footprints, CarFront,
  Sparkles, Clock3, ChevronRight, Check, CircleCheck, CircleAlert, X, History, SlidersHorizontal,
  Coffee, BedDouble, Landmark, Send, Route, LoaderCircle, Leaf, ExternalLink, Pause, Play } from 'lucide-vue-next'
import CityTransfers from './components/CityTransfers.vue'
import DestinationEditor from './components/DestinationEditor.vue'
import RouteChoices from './components/RouteChoices.vue'
import GuideInput from './components/GuideInput.vue'
import GuideEvidence from './components/GuideEvidence.vue'
import CityScene from './components/CityScene.vue'
import { timelineEnter as vTimelineEnter, usePageMotion } from './appearance/usePageMotion'
import JourneyBackdrop from './appearance/JourneyBackdrop.vue'
import RouteMap from './components/RouteMap.vue'
import StudyForm from './components/StudyForm.vue'
import StudyCorpusNotice from './components/StudyCorpusNotice.vue'
import StudyQA from './components/StudyQA.vue'
import ItineraryStop from './components/ItineraryStop.vue'
import BudgetDetails from './components/BudgetDetails.vue'
import ValidationCard from './components/ValidationCard.vue'
import WeatherCard from './components/WeatherCard.vue'
import PlanRecovery from './components/PlanRecovery.vue'
import PlaceGuide from './components/PlaceGuide.vue'
import { api, stream } from './api'
import { journeyCities, transferCoverage, transportRequestChanged, transportScopeText } from './transportScope'
import { studyThemes } from './studySuggestions'
import type { TripRequest, Plan, Health, HistoryItem, ToolEvent, ValidationReport, StudyCatalog, DestinationStay, Mode, RouteLeg, WeatherSnapshot } from './types'

const { reduced: reducedMotion, motion: pageMotion, setHero: setMotionHero, introPlaying, toggleMotion } = usePageMotion()

function localDate() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function travelDefaults() { return { destinations: [] as DestinationStay[], intercity_mode: 'train' as const, include_origin_transport: false, return_to_origin: false, guides: [], guide_mode: 'prefer' as const, compare_modes: true, confirmed_hotel: '' } }
const revisionScope = ref('all'), mapCity = ref('')
const form = reactive<TripRequest>({ ...travelDefaults(), destinations: [{ city: '上海', preferred_days: 3 }], origin: '', destination: '上海', start_date: localDate(), start_time: '09:00',
  days: 3, style: 'balanced', budget: 3000, people: 2, mode: 'auto', preferences: '', study: null })
const studyMode = ref(sessionStorage.getItem('travel-study-mode') === '1'), catalog = ref<StudyCatalog | null>(null)
const restoringStudy = ref(studyMode.value)
const savedForms: Record<string, TripRequest> = {
  travel: JSON.parse(JSON.stringify(form)) as TripRequest,
  study: { ...travelDefaults(), origin: '', destination: '上海', start_date: localDate(), start_time: '09:00', days: 1,
    style: 'relaxed', budget: 500, people: 2, mode: 'walking', preferences: '',
    study: { topic: '1920年前后的上海：青年与新思想', end_time: '13:00', max_stops: 3, max_walking_m: 3000, required_place_ids: [] } },
}
// StudyForm renders before onMounted; restore its required study fields synchronously.
if (studyMode.value) Object.assign(form, JSON.parse(JSON.stringify(savedForms.study)))
const savedPlans: Record<string, Plan | null> = { travel: null, study: null }
const health = ref<Health | null>(null), plan = ref<Plan | null>(null), history = ref<HistoryItem[]>([])
const historyOpen = ref(false), busy = ref(false), progress = ref(''), error = ref(''), tools = ref<ToolEvent[]>([])
const retainedPlanNotice = ref('')
const weatherOverrides = ref<Record<string, WeatherSnapshot>>({}), weatherRetryErrors = ref<Record<string, string>>({}), weatherRetryCity = ref('')
let weatherController: AbortController | null = null, weatherSequence = 0
watch(() => plan.value?.id, () => {
  weatherController?.abort(); weatherSequence++; weatherRetryCity.value = ''; weatherOverrides.value = {}; weatherRetryErrors.value = {}
}, { flush: 'sync' })
onBeforeUnmount(() => { weatherController?.abort(); weatherSequence++ })
const activeDay = ref(0), selectedStopId = ref(''), revision = ref(''), checking = ref(false)
const resultSection = ref<HTMLElement>()
let controller: AbortController | null = null
let runSequence = 0
const day = computed(() => plan.value?.itinerary.days[activeDay.value])
const selectedStudyPlace = computed(() => day.value?.stops.find(stop => stop.id === selectedStopId.value)?.study?.place_id)
const dayRoutes = computed(() => plan.value?.routes.filter(r => r.day_index === activeDay.value) || [])
const dayCities = computed(() => [...new Set((day.value?.stops || []).map(stop => stop.city).filter(Boolean))])
const mapStops = computed(() => (day.value?.stops || []).filter(stop => !mapCity.value || stop.city === mapCity.value))
const mapCities = computed(() => mapCity.value ? [mapCity.value] : dayCities.value.length ? dayCities.value : dayWeather.value.map(item => item.city))
const mapRoutes = computed(() => dayRoutes.value.filter(route => route.scope !== 'intercity' &&
  mapStops.value.some(stop => stop.id === route.from_id) && mapStops.value.some(stop => stop.id === route.to_id)))
const preferredDays = computed(() => form.destinations.reduce((sum, stay) => sum + Number(stay.preferred_days || 0), 0))
const hasIntercityTravel = computed(() => {
  const cities = form.destinations.map(stay => stay.city.trim().replace(/市$/, '')).filter(Boolean)
  const origin = form.origin.trim().replace(/市$/, '')
  return cities.length > 1 || Boolean(origin && cities[0] && origin !== cities[0] && (form.include_origin_transport || form.return_to_origin))
})
const transportPending = computed(() => !studyMode.value && Boolean(plan.value && transportRequestChanged(form, plan.value.request)))
const savedTransportCoverage = computed(() => plan.value ? transferCoverage(plan.value.request, plan.value.itinerary.transfers || []) : null)
const routeAfter = (id: string) => dayRoutes.value.find(r => r.from_id === id)
const dayWeather = computed(() => {
  const saved = plan.value?.weather_by_city || {}
  if (!Object.keys(saved).length) {
    const city = plan.value?.weather?.requested_city || plan.value?.request.destination || ''
    return [{ city, weather: weatherOverrides.value[city] || plan.value?.weather }]
  }
  const cities = dayCities.value.length ? dayCities.value : [...new Set((plan.value?.itinerary.transfers || []).filter(t => t.departure_date === day.value?.date || t.arrival_date === day.value?.date).flatMap(t => [t.from_city, t.to_city]))]
  return cities.filter(city => saved[city]).map(city => ({ city, weather: weatherOverrides.value[city] || saved[city] }))
})

async function retryWeather(city: string) {
  if (!plan.value || busy.value || checking.value || weatherRetryCity.value) return
  const planId = plan.value.id, sequence = ++weatherSequence
  weatherController = new AbortController(); weatherRetryCity.value = city; weatherRetryErrors.value[city] = ''
  try {
    const result = await api<WeatherSnapshot>(`/api/plans/${planId}/weather?city=${encodeURIComponent(city)}`, { method: 'POST', signal: weatherController.signal })
    if (plan.value?.id === planId && sequence === weatherSequence) weatherOverrides.value[city] = result
  } catch (e) {
    if (plan.value?.id === planId && sequence === weatherSequence && !(e instanceof DOMException && e.name === 'AbortError')) weatherRetryErrors.value[city] = e instanceof Error ? e.message : '天气重查未成功，请稍后再试'
  } finally {
    if (sequence === weatherSequence) { weatherRetryCity.value = ''; weatherController = null }
  }
}
const report = computed(() => plan.value?.validation)
const totalMinutes = computed(() => Math.ceil(dayRoutes.value.reduce((sum, r) => sum + (r.duration_s || 0), 0) / 60))
const totalDistance = computed(() => (dayRoutes.value.reduce((sum, r) => sum + (r.distance_m || 0), 0) / 1000).toFixed(1))
const hasMeasuredRoutes = computed(() => dayRoutes.value.some(route => route.scope !== 'intercity' && route.available && route.duration_s != null && route.distance_m != null))
const categoryNames: Record<string, string> = { ticket: '景点门票', food: '餐饮', hotel: '住宿', transport: '交通（含所选跨城）', other: '其他' }
const costs = computed(() => {
  const values = plan.value?.expense_totals || {}
  return Object.entries(values).map(([category, value]) => ({ category, name: categoryNames[category], value }))
})
const budgetRatio = computed(() => report.value ? Math.min(100, report.value.total_cents / report.value.budget_cents * 100) : 0)
const money = (cents: number) => (cents / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })

async function refreshHistory() { history.value = await api<HistoryItem[]>('/api/plans') }
onMounted(async () => {
  if (studyMode.value) {
    const saved = sessionStorage.getItem('travel-study-plan')
    if (saved) {
      try {
        const restored = await api<Plan>('/api/plans/' + encodeURIComponent(saved))
        if (restored.request.study) { plan.value = restored; savedPlans.study = restored; Object.assign(form, travelDefaults(), restored.request); setDay(0) }
      } catch { sessionStorage.removeItem('travel-study-plan') }
    }
  }
  restoringStudy.value = false
  try { health.value = await api<Health>('/api/health'); await refreshHistory() }
  catch { error.value = '无法连接本地服务，请确认启动窗口仍在运行。' }
  try { catalog.value = await api<StudyCatalog>('/api/study/catalog') }
  catch { catalog.value = { ready: false, message: '暂时无法读取研学资料状态', corpus_version: '', source_count: 0, fact_count: 0, topics: [], places: [] } }
})

watch([studyMode, () => plan.value?.id, restoringStudy], () => {
  if (restoringStudy.value) return
  sessionStorage.setItem('travel-study-mode', studyMode.value ? '1' : '0')
  if (studyMode.value) {
    if (plan.value?.id) sessionStorage.setItem('travel-study-plan', plan.value.id)
    else sessionStorage.removeItem('travel-study-plan')
  }
})

function switchMode(toStudy: boolean) {
  if (toStudy === studyMode.value) return
  controller?.abort(); runSequence++; busy.value = false; controller = null
  const current = studyMode.value ? 'study' : 'travel'
  savedForms[current] = JSON.parse(JSON.stringify(form)) as TripRequest
  savedPlans[current] = plan.value
  studyMode.value = toStudy
  const next = toStudy ? 'study' : 'travel'
  Object.assign(form, JSON.parse(JSON.stringify(savedForms[next])), { study: savedForms[next].study || null })
  plan.value = savedPlans[next]; error.value = ''; tools.value = []; revision.value = ''; retainedPlanNotice.value = ''
  setDay(0)
}

async function chooseStudyTheme(question: string) {
  if (busy.value || !studyMode.value || !form.study) return
  form.study.topic = question
  await nextTick()
  document.getElementById('study-topic')?.focus()
}

function setDay(index: number) {
  mapCity.value = ""
  activeDay.value = index
  selectedStopId.value = plan.value?.itinerary.days[index]?.stops[0]?.id || ''
}
function selectStop(id: string) {
  selectedStopId.value = id
  if (mapCity.value) mapCity.value = day.value?.stops.find(stop => stop.id === id)?.city || ''
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function destinationRows(request: TripRequest): DestinationStay[] {
  return request.destinations?.length
    ? request.destinations.map(stay => ({ city: stay.city, preferred_days: stay.preferred_days == null ? (request.destinations.length === 1 ? request.days : 1) : Number(stay.preferred_days) }))
    : request.destination.split(/\s*(?:→|->|、|，|,|；|;|\n)\s*/).filter(Boolean).map((city, _, cities) => ({ city: city.trim(), preferred_days: cities.length === 1 ? request.days : 1 }))
}
async function openHistory(id: string) {
  if (busy.value) return
  const token = ++runSequence
  try {
    const loaded = await api<Plan>(`/api/plans/${id}`)
    loaded.validation = await api<ValidationReport>(`/api/plans/${id}/validate`, { method: 'POST' })
    if (token !== runSequence) return
    const current = studyMode.value ? 'study' : 'travel'
    savedForms[current] = JSON.parse(JSON.stringify(form)) as TripRequest
    savedPlans[current] = plan.value
    plan.value = loaded
    studyMode.value = Boolean(plan.value.request.study)
    Object.assign(form, travelDefaults(), plan.value.request, { study: plan.value.request.study || null })
    if (!plan.value.request.study) form.destinations = destinationRows(plan.value.request)
    savedPlans[studyMode.value ? 'study' : 'travel'] = plan.value
    setDay(0); historyOpen.value = false; error.value = ''; tools.value = []; retainedPlanNotice.value = ''
  } catch (e) { if (token === runSequence) error.value = e instanceof Error ? e.message : '读取失败' }
}

async function run(isRevision = false, refresh = false) {
  if (busy.value || checking.value || (isRevision && (!plan.value || !revision.value.trim()))) return
  if (!studyMode.value && (form.destinations.length > 5 || (isRevision && destinationRows(plan.value!.request).length > 5))) {
    error.value = '最多五个目的地。旧六城方案请删减后点击“重新规划一份行程”。'; return
  }
  const destinations = form.destinations.map(stay => ({ city: stay.city.trim(), preferred_days: stay.preferred_days == null ? 1 : Number(stay.preferred_days) }))
  if (!studyMode.value && !refresh && !isRevision && destinations.some(stay => !stay.city)) { error.value = '请填写每一站的目的地城市。'; return }
  if (!studyMode.value && !refresh && !isRevision && destinations.some(stay => /→|->|、|，|,|；|;|\n/.test(stay.city))) {
    error.value = '每一行只填写一个城市，请用“添加目的地”增加下一站。'; return
  }
  if (!studyMode.value && !refresh && !isRevision && destinations.some((stay, index) => index > 0 && stay.city.replace(/市$/, '') === destinations[index - 1]!.city.replace(/市$/, ''))) {
    error.value = '相邻两站不能是同一个城市，请合并停留时间或调整顺序。'; return
  }
  if (!studyMode.value && !refresh && !isRevision && Math.ceil(preferredDays.value) > 10) {
    error.value = '各站游玩天数合计最多 10 天，请调整各城市时长。'; return
  }
  busy.value = true; error.value = ''; tools.value = []; retainedPlanNotice.value = ''; progress.value = '准备开始规划…'
  controller = new AbortController()
  const token = ++runSequence
  try {
    await nextTick(); resultSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    const trip = studyMode.value ? { ...form, destinations: [], mode: form.mode, days: 1, include_origin_transport: false, return_to_origin: false } : { ...form, mode: 'auto', compare_modes: true, destination: destinations.map(stay => stay.city).join('→'), destinations,
      days: undefined, people: Number(form.people), budget: Number(form.budget),
      guides: (form.guides || []).map((source, index) => ({ title: `参考攻略 ${index + 1}`, text: source.text })),
      include_origin_transport: Boolean(form.origin.trim() && (form.include_origin_transport || form.return_to_origin)),
      return_to_origin: Boolean(form.origin.trim() && form.return_to_origin) }
    await stream(refresh ? `/api/plans/${plan.value!.id}/refresh` : isRevision ? `/api/plans/${plan.value!.id}/revise` : '/api/plans/generate',
      refresh ? {} : isRevision ? { instruction: revision.value, trip, day_index: !studyMode.value && revisionScope.value === 'day' ? activeDay.value : null } : trip,
      controller.signal, (kind, data) => {
        if (token !== runSequence) return
        if (kind === 'status') progress.value = (data as { message: string }).message
        if (kind === 'tool') tools.value.push(data as ToolEvent)
        if (kind === 'result') {
          const generated = data as Plan
          if (!studyMode.value && !refresh && transportRequestChanged(trip, generated.request)) {
            throw new Error('返回的行程未保留本次提交的出发／返程交通选择，未替换当前显示。请重新规划，并核对结果中的交通范围。')
          }
          plan.value = generated; savedPlans[studyMode.value ? 'study' : 'travel'] = plan.value; setDay(0); revision.value = ''; progress.value = '行程已保存，请查看路线与待核实信息'
        }
      })
    await refreshHistory()
  } catch (e) {
    if (token !== runSequence) return
    if (e instanceof DOMException && e.name === 'AbortError') {
      progress.value = '本次规划已取消，已有方案仍然保留'
      if (plan.value) retainedPlanNotice.value = '本次规划已取消，下方仍是原有行程，交通选择和预算尚未更新。'
    } else {
      error.value = e instanceof Error ? e.message : '规划失败，请重试'
      if (plan.value) retainedPlanNotice.value = '本次未生成新行程，下方仍是原有版本，交通选择和预算尚未更新。'
    }
  } finally { if (token === runSequence) { busy.value = false; controller = null } }
}
async function chooseRoute(route: RouteLeg, mode: Mode) {
  if (!plan.value || busy.value) return
  busy.value = true; error.value = ''
  const token = ++runSequence
  controller = new AbortController()
  try {
    const updated = await api<Plan>(`/api/plans/${plan.value.id}/route`, { method: 'POST', signal: controller.signal, body: JSON.stringify({ day_index: route.day_index, from_id: route.from_id, to_id: route.to_id, mode, expected_routing_revision: plan.value.routing_revision || 0 }) })
    if (token !== runSequence) return
    plan.value = updated
    savedPlans.travel = updated
    progress.value = '交通选择已保存到当前行程，未新增版本；时间和费用已重算'
    await refreshHistory()
  } catch (e) { if (token === runSequence) error.value = e instanceof Error ? e.message : '更换路线失败' }
  finally { if (token === runSequence) { busy.value = false; controller = null } }
}
async function deleteHistory() {
  if (!plan.value || busy.value) return
  try {
    await api(`/api/plans/${plan.value.id}`, { method: 'DELETE' })
    savedPlans[studyMode.value ? 'study' : 'travel'] = null
    plan.value = null; await refreshHistory(); progress.value = '已删除所选版本，其他历史版本保留'
  } catch (e) { error.value = e instanceof Error ? e.message : '删除失败' }
}
async function validateAgain() {
  if (!plan.value || checking.value) return
  checking.value = true
  const current = plan.value
  try {
    const validation = await api<ValidationReport>(`/api/plans/${current.id}/validate`, { method: 'POST' })
    if (plan.value?.id === current.id) plan.value.validation = validation
  }
  catch (e) { if (plan.value?.id === current.id) error.value = e instanceof Error ? e.message : '校验失败' }
  finally { checking.value = false }
}
function reviseProblems() {
  revision.value = '请修正当前校验发现的问题，保留我的偏好。重新安排时间以满足真实路线时长，避免重复景点，并让全员估算费用不超过预算。'
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
    <JourneyBackdrop :motion="pageMotion" />
    <header class="topbar">
      <a class="brand" href="/" aria-label="知途首页"><span class="brand-symbol"><Compass :size="24" /></span><strong>知途<span>ZHITU</span></strong></a>
      <nav><a href="#planner" class="nav-active">规划旅程</a><button @click="historyOpen = true"><History :size="16" /> 我的行程 <span v-if="history.length" class="count">{{ history.length }}</span></button></nav>
      <div class="topbar-note"><span class="live-dot" :class="{ offline: !health?.capabilities.planning }"></span>{{ health ? (health.capabilities.planning ? '准备好出发了' : '检查服务配置') : '正在连接服务…' }}</div>
    </header>

    <section v-if="!plan" :ref="setMotionHero" class="hero" :class="{ 'appearance-intro': introPlaying }">
      <div class="hero-copy">
        <span class="eyebrow"><Sparkles :size="14" /> YOUR NEXT LITTLE ADVENTURE</span>
        <h1 v-if="studyMode">从一个问题，<br />走进<span>一段历史。</span></h1>
        <h1 v-else>好风景，<br />也要<span>好行程。</span></h1>
        <p v-if="studyMode">沿着人物、书刊与建筑的线索，<br class="desktop-break" />让有出处的故事，成为可以走近的研学旅程。</p>
        <p v-else>把期待告诉我们。让 AI 整理灵感，<br class="desktop-break" />让真实路线，把每一站串成你的旅程。</p>
        <a href="#planner" class="hero-link">{{ studyMode ? '开始我的研学探索' : '开始我的城市漫游' }} <ArrowUpRight :size="20" /></a>
        <div class="hero-features"><span><Route :size="15" /> 真实地图路线</span><span><Wallet :size="15" /> 全员预算核对</span><span><SlidersHorizontal :size="15" /> 随心调整行程</span></div>
      </div>
      <CityScene />
    </section>

    <main id="planner" class="planner">
      <div class="planning-modes" role="tablist" aria-label="规划模式"><button role="tab" :aria-selected="!studyMode" :class="{ active: !studyMode }" @click="switchMode(false)">普通旅行</button><button role="tab" :aria-selected="studyMode" :class="{ active: studyMode }" @click="switchMode(true)">历史研学<span>有据可循</span></button></div>
      <div class="section-heading"><div><span class="eyebrow">MAKE IT YOURS</span><h2>{{ plan ? '你的旅程，有了轮廓。' : '下一站，你想去哪里？' }}</h2></div></div>
      <StudyCorpusNotice v-if="studyMode" :catalog="catalog" />
      <section v-if="studyMode" class="study-demo-topics card" aria-labelledby="demo-topics-title">
        <div class="demo-topics-heading"><h3 id="demo-topics-title">从这些主题开始探索</h3><span>上海 · 单日研学</span></div>
        <p class="demo-topics-intro">选择主题填入问题，再设置时间、预算与出行方式，生成带出处的研学安排。</p>
        <div class="demo-topics-grid">
          <article v-for="(theme, index) in studyThemes" :key="theme.id" :class="{ selected: form.study?.topic === theme.question }">
            <span class="demo-topic-number">{{ String(index + 1).padStart(2, '0') }}</span><h4>{{ theme.title }}</h4><p>{{ theme.description }}</p>
            <button type="button" class="demo-topic-action" :disabled="busy" :aria-pressed="form.study?.topic === theme.question" :data-study-topic="theme.id" @click="chooseStudyTheme(theme.question)">{{ form.study?.topic === theme.question ? '已填入问题' : '用这个主题' }}<ArrowRight :size="14" /></button>
          </article>
        </div>
        <p class="demo-topics-help">也可以填写自己的问题，行程会结合时间与距离选择地点。生成后可查看原文、接着提问，并在“出行参考”中按地点查询开放与预约信息。</p>
      </section>
      <div class="planner-grid">
        <aside v-if="!studyMode" class="request-panel card">
          <div class="panel-heading"><div class="icon-tile"><SlidersHorizontal :size="19" /></div><div><h3>旅行偏好</h3><p>从几个小小的期待开始</p></div></div>
          <form @submit.prevent="run()">
            <fieldset :disabled="busy">
              <label class="field"><span>出发城市 <small>可选</small></span><div class="input-wrap"><Compass :size="17" /><input v-model="form.origin" maxlength="40" placeholder="例如：北京" /></div></label>
              <div class="journey-options">
                <label><input v-model="form.include_origin_transport" type="checkbox" :disabled="!form.origin.trim() || form.return_to_origin" /> 安排从出发地到首城的交通</label>
                <label><input v-model="form.return_to_origin" type="checkbox" :disabled="!form.origin.trim()" @change="form.include_origin_transport = form.include_origin_transport || form.return_to_origin" /> 最后返回出发地</label>
                <p v-if="!form.origin.trim()" class="journey-options-hint">请先填写出发城市，再选择出发或返程交通。</p>
              </div>
              <DestinationEditor v-model="form.destinations" :disabled="busy" />
              <p v-if="hasIntercityTravel" class="form-note" role="status">本次提交交通：{{ journeyCities(form).join(' → ') }}；铁路可查询参考车次，机票仅提供估算。</p>
              <p v-if="transportPending" class="stay-budget-note" role="status">当前交通选项与已显示行程不同。勾选不会改写旧行程，请点击“重新规划一份行程”使当前选择参与规划和预算。</p>
              <p v-if="Math.ceil(preferredDays) > 10" class="stay-budget-note" role="status">各站游玩天数合计最多 10 天，请减少部分城市的时长。</p>
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

        <aside v-else class="request-panel card"><StudyForm :model-value="form" :catalog="catalog" :busy="busy" :can-plan="Boolean(health?.capabilities.planning)" @update:model-value="Object.assign(form, $event)" @generate="run()" /><div v-if="health?.missing.length" class="config-notice"><CircleAlert :size="16" /><span>待配置：{{ health.missing.join('、') }}</span></div></aside>

        <section ref="resultSection" class="workspace" aria-live="polite">
          <StudyQA v-if="studyMode && !restoringStudy" :ready="Boolean(catalog?.ready)" :plan-id="plan?.id" :place-id="selectedStudyPlace" :place-name="day?.stops.find(stop => stop.id === selectedStopId)?.name" />
          <div v-if="error" class="error-banner" role="alert"><CircleAlert :size="19" /><div><strong>这次没能顺利完成</strong><p>{{ error }}</p></div><button aria-label="关闭错误提示" @click="error = ''"><X :size="17" /></button></div>
          <div v-if="busy" class="progress-card card"><div class="progress-icon"><Compass :size="26" class="spin-slow" /></div><div><span class="eyebrow">A LITTLE PLANNING, A LOT TO DISCOVER</span><h3>{{ progress }}</h3><p>{{ plan ? "正在按当前选择生成新行程；下方暂为原有版本，新结果返回后才会更新交通与预算。" : "正在结合地点、路线和你的旅行偏好。通常需要几十秒。" }}</p></div><button class="text-button" @click="controller?.abort()">取消</button><div class="progress-line"></div></div>

          <template v-if="plan && day">
            <p v-if="retainedPlanNotice" class="stay-budget-note card" role="status">{{ retainedPlanNotice }}</p>
            <div class="plan-heading card"><div><span class="eyebrow"><MapPin :size="13" /> {{ plan.request.destination }} · {{ plan.request.days }} 天 · {{ plan.request.people }} 人</span><h2>{{ plan.itinerary.title }}</h2><p>{{ plan.itinerary.summary }}</p></div><span class="revision-badge">版本 {{ plan.revision }}</span></div>
            <CityTransfers v-if="!studyMode" :transfers="plan.itinerary.transfers || []" :request="plan.request" />
            <PlanRecovery :plan="plan" :busy="busy" :checking="checking" @refresh="run(false, true)" @revise="reviseProblems" />
            <div class="day-tabs" role="tablist" aria-label="选择行程日期"><button v-for="(d, i) in plan.itinerary.days" :key="d.date" role="tab" :aria-selected="activeDay === i" :class="{ active: activeDay === i }" @click="setDay(i)"><span>DAY {{ String(i + 1).padStart(2, '0') }}</span><strong>{{ d.date.slice(5).replace('-', ' / ') }}</strong></button></div>
            <WeatherCard v-for="item in dayWeather" :key="item.city" :weather="item.weather" :city="item.city" :date="day.date" :retrying="weatherRetryCity === item.city" :retry-disabled="busy || checking || Boolean(weatherRetryCity)" :refreshed="Boolean(weatherOverrides[item.city])" :retry-error="weatherRetryErrors[item.city]" @retry="retryWeather(item.city)" />
            <div class="journey-layout">
              <div v-timeline-enter="activeDay" class="day-itinerary card"><div class="day-title"><span class="icon-tile"><Route :size="19" /></span><div><h3>{{ day.title }}</h3><p><template v-if="!studyMode">{{ day.stops.length }} 个活动 · </template>{{ plan.request.mode === 'walking' ? '步行' : '驾车' }}<template v-if="studyMode"> · 沿着资料寻找线索</template></p></div></div>
                <div class="timeline">
                  <template v-for="(stop, i) in day.stops" :key="stop.id">
                    <ItineraryStop :stop="stop" :index="i" :selected="selectedStopId === stop.id" :context="plan.study_context" :expenses="day.expenses.filter(cost => cost.stop_id === stop.id && cost.category !== 'transport')" :people="plan.request.people" @select="selectStop" />
                    <p v-if="!studyMode && i < day.stops.length - 1 && routeAfter(stop.id)?.scope === 'intercity'" class="transfer">跨城移动 · 见上方交通安排</p>
                    <RouteChoices v-else-if="!studyMode && i < day.stops.length - 1 && routeAfter(stop.id)" :route="routeAfter(stop.id)!" :disabled="busy || checking" @select="chooseRoute(routeAfter(stop.id)!, $event)" />
                    <div v-else-if="i < day.stops.length - 1" class="transfer"><component :is="plan.request.mode === 'walking' ? Footprints : CarFront" :size="13" /><span v-if="dayRoutes[i]?.available">{{ Math.ceil((dayRoutes[i]!.duration_s || 0) / 60) }} 分钟 · {{ ((dayRoutes[i]!.distance_m || 0) / 1000).toFixed(1) }} km</span><span v-else>路线暂不可用</span></div>
                  </template>
                </div>
                <div v-if="day.meal_note" class="accommodation"><Coffee :size="17" /><div><strong>用餐提醒</strong><p>{{ day.meal_note }}</p></div></div>
                <div v-if="day.accommodation" class="accommodation"><BedDouble :size="17" /><div><strong>今晚住哪里</strong><p>{{ day.accommodation }}</p><small>住宿建议与价格需要自行核实</small></div></div>
              </div>
              <div class="journey-sidebar"><div class="map-panel card"><div class="map-heading"><div><MapPin :size="17" /><strong>把行程放到地图上</strong></div><span>点击标记查看地点</span></div><div v-if="dayCities.length > 1" class="map-city-tabs" role="group" aria-label="选择地图城市"><button :class="{ active: !mapCity }" @click="mapCity = ''">当天全部</button><button v-for="city in dayCities" :key="city" :class="{ active: mapCity === city }" @click="mapCity = city">{{ city }}</button></div><RouteMap :cities="mapCities" :stops="mapStops" :routes="mapRoutes" :selected-id="selectedStopId" :busy="busy || checking" :can-refresh="!studyMode && mapStops.some(stop => stop.location_behavior !== 'unlocated')" :can-revise="mapStops.some(stop => stop.location_behavior === 'unlocated')" @select="selectStop" @retry="run(false, true)" @revise="reviseProblems" /><div class="map-stats"><div><Route :size="16" /><span>{{ hasMeasuredRoutes ? totalDistance : '—' }} <small>km</small></span><label>已查到的路线总长</label></div><div><Clock3 :size="16" /><span>{{ hasMeasuredRoutes ? totalMinutes : '—' }} <small>分钟</small></span><label>已查到的通行时长</label></div></div><p class="map-footnote">仅展示高德返回的真实路线；缺失路线不会用直线代替。路况变化会影响实际耗时。</p></div>
              <PlaceGuide :plan-id="plan.id" :stop="day.stops.find(stop => stop.id === selectedStopId)" :stops="day.stops" :date="day.date" :disabled="busy" :study-context="plan.study_context" @select="selectedStopId = $event" /></div>
            </div>

            <div class="insights-grid">
              <div class="budget-card card"><div class="mini-heading"><Wallet :size="18" /><h3>旅行预算</h3><span>全员 · 全程估算</span></div><div class="budget-amount"><strong>¥{{ money(report!.total_cents) }}</strong><span>/ ¥{{ money(report!.budget_cents) }}</span></div><div class="budget-bar"><span :style="{ width: budgetRatio + '%' }" :class="{ over: report!.total_cents > report!.budget_cents }"></span></div><div class="cost-list"><div v-for="cost in costs" :key="cost.category"><span>{{ cost.name }}</span><strong>¥{{ money(cost.value) }}</strong></div></div><p v-if="report!.total_cents > report!.budget_cents" class="budget-excess">当前方案超出预算 ¥{{ money(report!.total_cents - report!.budget_cents) }}，尚未满足预算要求。</p><p v-if="!studyMode && savedTransportCoverage && !savedTransportCoverage.complete" class="budget-excess">跨城交通尚有缺项，当前合计不代表完整旅费。</p><p v-if="!studyMode && plan.request.origin" class="subtle-note">本版本交通范围：{{ transportScopeText(plan.request) }}</p><BudgetDetails :itinerary="plan.itinerary" :people="plan.request.people" /><p class="subtle-note">所有费用均为估算，不代表实时票价或酒店报价。</p></div>
              <ValidationCard :key="plan.id" :report="plan.validation" :days="plan.itinerary.days" :busy="busy" :checking="checking" @revalidate="validateAgain" @revise="reviseProblems" />
            </div>
            <div class="plan-actions card"><button v-if="!studyMode" class="secondary-button" :disabled="busy || checking" @click="run(false, true)">更新路线与顺路餐饮</button><button v-if="!studyMode && form.guides?.length" class="text-button" :disabled="busy || checking" @click="followGuides">按攻略优先重新规划</button><small>活动修改保留新版本；普通旅行切换交通或仅更新路线时保存到当前版本。</small><button class="text-button" :disabled="busy || checking" @click="deleteHistory">删除当前历史版本</button></div>
            <GuideEvidence v-if="!studyMode" :guides="plan.request.guides || []" :decisions="plan.itinerary.guide_decisions || []" :stops="plan.itinerary.days.flatMap(d => d.stops)" />
            <div v-if="plan.adjustments?.length" class="travel-tips"><Clock3 :size="17" /><div><strong>时间调整记录</strong><p v-for="item in plan.adjustments" :key="item">{{ item }}</p></div></div>
            <div class="revise-card card"><div class="mini-heading"><Sparkles :size="19" /><h3>{{ studyMode ? '调整这次研学安排' : '让它更像你的旅行' }}</h3><span>每次调整保留旧版本</span></div><p>{{ studyMode ? '改变可用时间、保留喜欢的地点，或调整讲解重点。问历史知识请使用上方资料问答。' : '想少走一点、换个景点，或让第二天更轻松？' }}</p><label v-if="!studyMode" class="field"><span>调整范围</span><select v-model="revisionScope" :disabled="busy"><option value="all">整个行程</option><option value="day">仅当前第 {{ activeDay + 1 }} 天</option></select></label><form class="revision-input" @submit.prevent="run(true)"><input v-model="revision" maxlength="2000" :disabled="busy" :placeholder="studyMode ? '例如：缩短到两小时，保留新渔阳里' : '例如：第二天少安排一个景点，多留些休息时间'" aria-label="行程修改要求" /><button type="submit" :disabled="busy || !revision.trim()" aria-label="提交行程修改"><Send :size="18" /></button></form></div>
            <div v-if="plan.itinerary.guide_insights?.length" class="travel-tips"><Landmark :size="17" /><div><strong>攻略参考摘要（经验信息，未经官方核实）</strong><article v-for="(insight, i) in plan.itinerary.guide_insights" :key="i"><p>{{ insight.text }}</p><a v-if="plan.request.guides?.[insight.source_index]?.url" :href="plan.request.guides[insight.source_index]!.url" target="_blank" rel="noopener noreferrer">{{ plan.request.guides[insight.source_index]!.title }}</a><small v-else>{{ plan.request.guides?.[insight.source_index]?.title }}</small></article></div></div>
            <div v-if="plan.itinerary.tips.length" class="travel-tips"><Leaf :size="17" /><div><strong>一点旅行小提醒</strong><p v-for="tip in plan.itinerary.tips" :key="tip">{{ tip }}</p></div></div>
          </template>

          <div v-else-if="!busy" class="empty-workspace card">
            <div class="empty-heading"><span class="eyebrow">A JOURNEY WAITING TO HAPPEN</span><span class="outline-tag"><Compass :size="14" /> {{ studyMode ? '从原文出发的研学' : '专属于你的行程' }}</span></div>
            <div class="empty-art"><div class="art-ring ring-one"></div><div class="art-ring ring-two"></div><div class="art-compass"><Compass :size="58" stroke-width="1.1" /></div><span class="art-chip chip-one"><component :is="studyMode ? Landmark : Coffee" :size="16" /> {{ studyMode ? '一段原文线索' : '一杯本地咖啡' }}</span><span class="art-chip chip-two"><Landmark :size="16" /> {{ studyMode ? '一处历史地点' : '一段城市故事' }}</span><span class="art-chip chip-three"><Leaf :size="16" /> {{ studyMode ? '一个现场问题' : '一点留白的时间' }}</span></div>
            <h3>{{ studyMode ? '让一段历史，有迹可循。' : '旅程，从你的期待开始。' }}</h3><p v-if="studyMode">提出一个学习问题，从资料里寻找人物与地点，<br />生成带出处的讲解、现场任务与真实行走路线。</p><p v-else>填写左侧的旅行偏好，我们会为你整理每日行程，<br />计算真实路线，并检查时间与预算是否合适。</p>
            <div class="empty-steps"><div><span>01</span><strong>{{ studyMode ? '选一个历史问题' : '说说你的期待' }}</strong><small>{{ studyMode ? '主题、时间与预算' : '目的地、时间与偏好' }}</small></div><ArrowRight :size="16" /><div><span>02</span><strong>{{ studyMode ? '把资料连成路线' : '看见旅行轮廓' }}</strong><small>{{ studyMode ? '地点、地图与原文依据' : '每日行程与真实路线' }}</small></div><ArrowRight :size="16" /><div><span>03</span><strong>{{ studyMode ? '边走边问与观察' : '调整成你的节奏' }}</strong><small>{{ studyMode ? '文字、照片与连续追问' : '校验问题，一次次完善' }}</small></div></div>
            <div class="empty-bottom"><CircleCheck :size="14" /> {{ studyMode ? '选主题只会填入问题，点击生成后才开始规划。' : '不需要下载大模型或 PDF 权重，就能开始规划。' }}</div>
          </div>
          <details v-if="tools.length" class="tool-log card"><summary><Route :size="16" /> 查看本次查询记录 <span>{{ tools.length }} 次工具调用</span></summary><div class="tool-log-body"><article v-for="(tool, i) in tools" :key="i"><strong>{{ tool.name }}</strong><code>{{ JSON.stringify(tool.arguments) }}</code><p>{{ tool.summary }}</p></article></div></details>
          <p v-if="progress && !busy && !error" class="completion-note"><Check :size="14" /> {{ progress }}</p>
        </section>
      </div>
    </main>
    <footer><a class="footer-brand" href="#"><Compass :size="17" /> 知途 · ZHITU</a><p>认真规划，自在出发。</p><button class="motion-control" type="button" :aria-pressed="pageMotion" :disabled="reducedMotion" @click="toggleMotion"><Pause v-if="pageMotion" :size="13" /><Play v-else :size="13" />{{ reducedMotion ? '系统已减少动态效果' : pageMotion ? '动态效果：开' : '动态效果：关' }}</button><a href="https://lbs.amap.com/" target="_blank" rel="noopener noreferrer">地图与路线由高德提供 <ExternalLink :size="12" /></a></footer>

    <Transition name="appearance-modal">
    <div v-if="historyOpen" class="modal-backdrop" @click.self="historyOpen = false"><section class="history-modal card" role="dialog" aria-modal="true" aria-labelledby="history-title"><div class="mini-heading"><History :size="20" /><h3 id="history-title">我的行程</h3><button class="icon-button" aria-label="关闭行程历史" @click="historyOpen = false"><X :size="20" /></button></div><p v-if="!history.length" class="history-empty">还没有保存的行程。完成第一次规划后，会自动出现在这里。</p><button v-for="item in history" :key="item.id" class="history-item" :disabled="busy" @click="openHistory(item.id)"><span class="icon-tile"><MapPin :size="20" /></span><div><strong>{{ item.title }}</strong><span>{{ item.destination }} · {{ item.start_date }} · 版本 {{ item.revision }}</span></div><ChevronRight :size="18" /></button></section></div>
    </Transition>
  </div>
</template>

<style scoped>
.budget-excess { color: #a65a32; font-size: 12px; line-height: 1.7; margin-top: 14px; }
.demo-topics-grid article { display: flex; flex-direction: column; min-width: 0; }
.demo-topics-grid article.selected { border-color: #9ac4b0; background: #f0f7ee; }
.demo-topic-action { display: inline-flex; align-items: center; gap: 7px; align-self: flex-start; padding: 8px 0 0; margin-top: auto; color: #147b71; font-size: 12px; }
.demo-topic-action:disabled { opacity: .5; cursor: not-allowed; }
.demo-topic-action:focus-visible { outline: 2px solid #147b71; outline-offset: 4px; border-radius: 3px; }
.demo-topics-help { font-size: 11px; line-height: 1.9; color: #74856a; margin: 16px 0 0; }
@media (min-width: 651px) and (max-width: 1050px) { .demo-topics-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
