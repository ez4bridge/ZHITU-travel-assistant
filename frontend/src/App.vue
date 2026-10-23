<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Compass, ArrowUpRight, ArrowRight, MapPin, Users, Wallet, Footprints, CarFront,
  Sparkles, Clock3, ChevronRight, ChevronDown, Trash2, Check, CircleCheck, CircleAlert, X, History, SlidersHorizontal,
  Coffee, BedDouble, Landmark, Send, Route, LoaderCircle, Leaf, ExternalLink, Pause, Play } from 'lucide-vue-next'
import DailyTransfer from './components/DailyTransfer.vue'
import ConstraintReplan from './components/ConstraintReplan.vue'
import BookingEditor from './components/BookingEditor.vue'
import PreparationCard from './components/PreparationCard.vue'
import HotelFields from './components/HotelFields.vue'
import TransportFields from './components/TransportFields.vue'
import { buildDayTimeline } from './dayTimeline'
import { groupTravelTips } from './dayTips'
import DestinationEditor from './components/DestinationEditor.vue'
import RouteChoices from './components/RouteChoices.vue'
import GuideInput from './components/GuideInput.vue'
import GuideEvidence from './components/GuideEvidence.vue'
import CityScene from './components/CityScene.vue'
import { timelineEnter as vTimelineEnter, usePageMotion } from './appearance/usePageMotion'
import JourneyBackdrop from './appearance/JourneyBackdrop.vue'
import RouteMap from './components/RouteMap.vue'
import StudyForm from './components/StudyForm.vue'
import StudyQA from './components/StudyQA.vue'
import ItineraryStop from './components/ItineraryStop.vue'
import BudgetDetails from './components/BudgetDetails.vue'
import ValidationCard from './components/ValidationCard.vue'
import WeatherCard from './components/WeatherCard.vue'
import PlanRecovery from './components/PlanRecovery.vue'
import PlaceGuide from './components/PlaceGuide.vue'
import { api, stream } from './api'
import { journeyCities, transferCoverage, transportRequestChanged, transportScopeText } from './transportScope'
import type { TripRequest, Plan, Health, HistoryItem, ToolEvent, ValidationReport, StudyCatalog, DestinationStay, Mode, RouteLeg, WeatherSnapshot, IntercityMode } from './types'

const { reduced: reducedMotion, motion: pageMotion, setHero: setMotionHero, introPlaying, toggleMotion } = usePageMotion()

function localDate() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function travelDefaults() { return { destinations: [] as DestinationStay[], intercity_mode: 'train' as const, intercity_modes: ['train', 'flight'] as IntercityMode[], include_origin_transport: false, return_to_origin: false, guides: [], guide_mode: 'prefer' as const, compare_modes: true, confirmed_hotel: '', confirmed_hotels: [], booked_transports: [] } }
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
const constraintBusy = ref(false), bookingBusy = ref(false)
const operationBusy = computed(() => busy.value || constraintBusy.value || bookingBusy.value)
const adjustmentPanel = ref<InstanceType<typeof ConstraintReplan>>()
const bookingPanel = ref<InstanceType<typeof BookingEditor>>()
const intercityOptions: { value: IntercityMode; label: string }[] = [{ value: 'train', label: '铁路（高铁／火车）' }, { value: 'flight', label: '飞机' }, { value: 'driving', label: '自驾' }, { value: 'bus', label: '长途汽车' }]
const travelTips = computed(() => plan.value ? groupTravelTips(plan.value) : { days: [], general: [] })
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
const timeline = computed(() => plan.value ? buildDayTimeline(plan.value, activeDay.value) : [])
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
const routeAfter = (id: string) => {
  const stops = day.value?.stops || []
  const index = stops.findIndex(stop => stop.id === id)
  const next = index >= 0 ? stops[index + 1] : undefined
  return next ? dayRoutes.value.find(route => route.from_id === id && route.to_id === next.id) : undefined
}
function canQueryRoute(route?: RouteLeg) {
  if (!route || route.scope !== 'local' || route.stationary || !day.value) return false
  const from = day.value.stops.find(stop => stop.id === route.from_id)
  const to = day.value.stops.find(stop => stop.id === route.to_id)
  return Boolean(from?.location_status === 'verified' && from.location && to?.location_status === 'verified' && to.location && from.city.replace(/市$/, '') === to.city.replace(/市$/, ''))
}
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
  if (!plan.value || operationBusy.value || checking.value || weatherRetryCity.value) return
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
const reminderCount = computed(() => {
  const value = report.value
  const findings = value ? value.errors.length + value.warnings.length + value.unknowns.length : 0
  return findings + (plan.value?.adjustments?.length || 0) + (travelTips.value?.general.length || 0)
})
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
  if (toStudy === studyMode.value || constraintBusy.value || bookingBusy.value) return
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

function setDay(index: number) {
  mapCity.value = ""
  activeDay.value = index
  selectedStopId.value = plan.value?.itinerary.days[index]?.stops[0]?.id || ''
}
function jumpToIssue(target: { day_index?: number | null; stop_id?: string | null; target?: string }) {
  if (target.target === 'budget') {
    document.querySelector('.budget-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    return
  }
  if (target.target === 'validation') {
    document.querySelector('.validation-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  const index = target.day_index
  if (typeof index !== 'number' || !plan.value?.itinerary.days[index]) return
  setDay(index)
  if (target.stop_id) selectedStopId.value = target.stop_id
  nextTick(() => document.querySelector('.day-itinerary')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
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
  if (operationBusy.value || checking.value) return
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
    if (!plan.value.request.study) {
      form.destinations = destinationRows(plan.value.request)
      form.intercity_modes = [...(plan.value.request.intercity_modes || [plan.value.request.intercity_mode])]
    }
    savedPlans[studyMode.value ? 'study' : 'travel'] = plan.value
    setDay(0); historyOpen.value = false; error.value = ''; tools.value = []; retainedPlanNotice.value = ''
  } catch (e) { if (token === runSequence) error.value = e instanceof Error ? e.message : '读取失败' }
}

async function run(isRevision = false, refresh = false) {
  if (operationBusy.value || checking.value || (isRevision && (!plan.value || !revision.value.trim()))) return
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
  if (!studyMode.value && !refresh && hasIntercityTravel.value && !form.intercity_modes?.length) { error.value = '请至少选择一种可接受的跨城交通方式。'; return }
  if (!studyMode.value && !refresh && !isRevision) {
    const booked = form.booked_transports || []
    const incomplete = booked.findIndex(item => !item.from_city.trim() || !item.to_city.trim()
      || !item.departure_date || !item.departure_time || !item.arrival_date || !item.arrival_time)
    if (incomplete >= 0) { error.value = `已确认交通 ${incomplete + 1} 需要补全出发／到达城市与时间。`; return }
    const backwards = booked.findIndex(item =>
      `${item.arrival_date}T${item.arrival_time}` <= `${item.departure_date}T${item.departure_time}`)
    if (backwards >= 0) { error.value = `已确认交通 ${backwards + 1} 的到达时间需要晚于出发时间；跨夜请修改到达日期。`; return }
    const costless = booked.findIndex(item => item.total_cost != null && (!Number.isFinite(item.total_cost) || item.total_cost < 0))
    if (costless >= 0) { error.value = `已确认交通 ${costless + 1} 的全员总费用需要是非负金额，不清楚请留空。`; return }
  }
  busy.value = true; error.value = ''; tools.value = []; retainedPlanNotice.value = ''; progress.value = '准备开始规划…'
  controller = new AbortController()
  const token = ++runSequence
  try {
    await nextTick(); resultSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    const trip = studyMode.value ? { ...form, destinations: [], mode: form.mode, days: 1, include_origin_transport: false, return_to_origin: false } : { ...form, intercity_mode: form.intercity_modes?.[0] || form.intercity_mode, mode: 'auto', compare_modes: true, destination: destinations.map(stay => stay.city).join('→'), destinations,
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
      const message = e instanceof Error ? e.message : '规划失败，请重试'
      error.value = message
      if (plan.value) {
        if (message.includes('已确认约束或途中事实')) {
          // The plan was already re-planned through constraints; the legacy
          // refresh/revise path is intentionally closed. Point to the working
          // entries instead of leaving a dead end.
          retainedPlanNotice.value = '这份行程已经有确认过的酒店／交通／调整：要按新条件整份重排，请用「补充酒店与已订交通」里的“按新条件整份重新生成并保存”；要局部调整，请用「调整行程」。'
        } else {
          retainedPlanNotice.value = '本次未生成新行程，下方仍是原有版本，交通选择和预算尚未更新。'
        }
      }
    }
  } finally { if (token === runSequence) { busy.value = false; controller = null } }
}
async function chooseRoute(route: RouteLeg, mode: Mode) {
  if (!plan.value || operationBusy.value || checking.value) return
  if (!studyMode.value) { await adjustmentPanel.value?.openWithRoute(route, mode); return }
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
  if (!plan.value || operationBusy.value || checking.value) return
  try {
    await api(`/api/plans/${plan.value.id}`, { method: 'DELETE' })
    savedPlans[studyMode.value ? 'study' : 'travel'] = null
    plan.value = null; await refreshHistory(); progress.value = '已删除所选版本，其他历史版本保留'
  } catch (e) { error.value = e instanceof Error ? e.message : '删除失败' }
}
async function validateAgain() {
  if (!plan.value || checking.value || operationBusy.value) return
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
  if (!studyMode.value) {
    adjustmentPanel.value?.openWithInstruction('请修正当前校验发现的问题，保留我的偏好。重新安排时间以满足真实路线时长，避免重复景点，并让全员估算费用不超过预算。尽量满足各城期望游玩时长；无法满足时明确说明原因。')
    return
  }
  revision.value = '请修正当前校验发现的问题，保留我的偏好。重新安排时间以满足真实路线时长，避免重复景点，并让全员估算费用不超过预算。'
  run(true)
}
function followGuides() {
  form.guide_mode = 'prefer'
  if (!studyMode.value) { adjustmentPanel.value?.openWithInstruction('请优先按我粘贴的攻略地点、游玩顺序和玩法重新规划；保留日期、城市与预算等约束，逐条列出原文摘录、对应活动，说明调整或未采纳原因。'); return }
  revisionScope.value = 'all'
  revision.value = '请优先按我粘贴的攻略地点、游玩顺序和玩法重新规划；保留日期、城市与预算等约束，逐条列出原文摘录、对应活动，说明调整或未采纳原因。'
  run(true)
}
async function acceptConstraintPlan(updated: Plan) {
  plan.value = updated; savedPlans.travel = updated
  form.budget = updated.request.budget; form.start_time = updated.request.start_time
  form.confirmed_hotel = updated.request.confirmed_hotel || ''
  form.confirmed_hotels = JSON.parse(JSON.stringify(updated.request.confirmed_hotels || []))
  form.booked_transports = JSON.parse(JSON.stringify(updated.request.booked_transports || []))
  setDay(Math.min(activeDay.value, updated.itinerary.days.length - 1))
  error.value = ''; revision.value = ''; tools.value = []; retainedPlanNotice.value = ''
  retainedPlanNotice.value = `已应用调整，当前显示第 ${updated.revision} 版；旧版本仍保留在“我的行程”。`
  progress.value = '已打开确认后的行程版本；可继续通过“调整行程”修改偏好、活动或途中进度'
  try { await refreshHistory() }
  catch { error.value = '行程已打开，但历史列表未能刷新，请稍后重试。' }
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
        <p v-if="studyMode">沿着人物、报刊与城市的线索，<br class="desktop-break" />让有出处的故事，成为可以走近的研学旅程。</p>
        <p v-else>把期待告诉我们。让 AI 整理灵感，<br class="desktop-break" />让真实路线，把每一站串成你的旅程。</p>
        <a href="#planner" class="hero-link">开始我的城市漫游 <ArrowUpRight :size="20" /></a>
        <div class="hero-features"><span><Route :size="15" /> 真实地图路线</span><span><Wallet :size="15" /> 全员预算核对</span><span><SlidersHorizontal :size="15" /> 随心调整行程</span></div>
      </div>
      <CityScene />
    </section>

    <main id="planner" class="planner">
      <div class="planning-modes" role="tablist" aria-label="规划模式"><button role="tab" :disabled="constraintBusy || bookingBusy" :aria-selected="!studyMode" :class="{ active: !studyMode }" @click="switchMode(false)">普通旅行</button><button role="tab" :disabled="constraintBusy || bookingBusy" :aria-selected="studyMode" :class="{ active: studyMode }" @click="switchMode(true)">历史研学<span>有据可循</span></button></div>
      <div class="section-heading"><div><span class="eyebrow">MAKE IT YOURS</span><h2>{{ plan ? '你的旅程，有了轮廓。' : '下一站，你想去哪里？' }}</h2></div></div>
      <section v-if="studyMode" class="study-demo-topics card" aria-labelledby="demo-topics-title">
        <div class="demo-topics-heading"><h3 id="demo-topics-title">当前 Demo 可以探索什么？</h3><span>上海近代史 · 1920年前后</span></div>
        <p class="demo-topics-intro">沿着现有资料，从人物、书刊与城市旧址出发，选择一个感兴趣的问题。</p>
        <div class="demo-topics-grid">
          <article><span class="demo-topic-number">01</span><h4>青年与新思想</h4><p>从《新青年》与渔阳里，了解青年如何阅读、学习并参与组织活动。</p></article>
          <article><span class="demo-topic-number">02</span><h4>一本书的传播</h4><p>围绕陈望道与《共产党宣言》，追踪翻译、印刷出版与思想传播的联系。</p></article>
          <article><span class="demo-topic-number">03</span><h4>城市里的红色记忆</h4><p>走近编辑部、印刷所和青年团旧址，将人物故事与今天的参观点连接起来。</p></article>
        </div>
      </section>
      <div class="planner-grid">
        <aside v-if="!studyMode" class="request-panel card">
          <div class="panel-heading"><div class="icon-tile"><SlidersHorizontal :size="19" /></div><div><h3>旅行偏好</h3><p>从几个小小的期待开始</p></div></div>
          <form @submit.prevent="run()">
            <fieldset :disabled="operationBusy || checking">
              <label class="field"><span>出发城市 <small>可选</small></span><div class="input-wrap"><Compass :size="17" /><input v-model="form.origin" maxlength="40" placeholder="例如：北京" /></div></label>
              <div class="journey-options">
                <label><input v-model="form.include_origin_transport" type="checkbox" :disabled="!form.origin.trim() || form.return_to_origin" /> 安排从出发地到首城的交通</label>
                <label><input v-model="form.return_to_origin" type="checkbox" :disabled="!form.origin.trim()" @change="form.include_origin_transport = form.include_origin_transport || form.return_to_origin" /> 最后返回出发地</label>
                <p v-if="!form.origin.trim()" class="journey-options-hint">请先填写出发城市，再选择出发或返程交通。</p>
              </div>
              <DestinationEditor v-model="form.destinations" :disabled="operationBusy || checking" />
              <p v-if="hasIntercityTravel" class="form-note" role="status">本次提交交通：{{ journeyCities(form).join(' → ') }}；铁路可查询参考车次，机票仅提供估算。</p>
              <p v-if="transportPending" class="stay-budget-note" role="status">当前交通选项与已显示行程不同。勾选不会改写旧行程，请点击“重新规划一份行程”使当前选择参与规划和预算。</p>
              <p v-if="Math.ceil(preferredDays) > 10" class="stay-budget-note" role="status">各站游玩天数合计最多 10 天，请减少部分城市的时长。</p>
              <div class="field-row"><label class="field"><span>出行日期</span><div class="input-wrap"><input v-model="form.start_date" type="date" required aria-label="出行日期" /></div></label><label class="field time-field"><span>首日开始</span><div class="input-wrap"><input v-model="form.start_time" type="time" required aria-label="首日开始时间" /></div></label></div>
              <label class="field"><span>同行人数</span><div class="input-wrap"><Users :size="16" /><input v-model.number="form.people" type="number" min="1" max="50" required /><span class="input-unit">人</span></div></label>
              <label class="field"><span>全员总预算 <small>全程含所选跨城交通</small></span><div class="input-wrap"><Wallet :size="17" /><input v-model.number="form.budget" type="number" min="1" max="10000000" step="0.01" required /><span class="input-unit">元</span></div></label>
              <div class="field"><span>旅行节奏</span><div class="segmented"><button type="button" :class="{ selected: form.style === 'relaxed' }" @click="form.style = 'relaxed'">慢慢逛</button><button type="button" :class="{ selected: form.style === 'balanced' }" @click="form.style = 'balanced'">刚刚好</button><button type="button" :class="{ selected: form.style === 'compact' }" @click="form.style = 'compact'">多看些</button></div></div>
              <div v-if="hasIntercityTravel" class="field"><span>可接受的跨城交通（可多选）</span><div class="intercity-preferences" role="group" aria-label="跨城交通偏好多选"><label v-for="option in intercityOptions" :key="option.value"><input v-model="form.intercity_modes" type="checkbox" :value="option.value" />{{ option.label }}</label></div><small class="form-note">逐段从所选方式中安排，可远程飞机、近程铁路。机票目前提供外部查询；估算不代表实际班次。</small></div>
              <p class="form-note">市内交通结合步行距离、通行时间与全员费用推荐；可比较步行、公交地铁和打车，再逐段预览确认。</p>
              <details class="hotel-input"><summary>已确认的酒店（可选，可填写多家）</summary><p class="subtle-note">尚未订好可以先生成；之后补录各城酒店和车票／机票，再核对接驳与时间。</p><HotelFields v-model="form.confirmed_hotels!" :cities="form.destinations.map(stay => stay.city).filter(Boolean)" :start-date="form.start_date" :days="Math.ceil(preferredDays)" compact :disabled="operationBusy || checking" /></details>
              <details class="hotel-input"><summary>已确认的交通（可选，可填写多段）</summary><p class="subtle-note">已经买好的车票／机票作为固定事实参与规划：模型按你填的时刻与站点安排当天活动。没订好可以留空，生成后再补录。</p><TransportFields v-model="form.booked_transports!" :cities="[form.origin, ...form.destinations.map(stay => stay.city)].filter(Boolean)" :start-date="form.start_date" :disabled="operationBusy || checking" /></details>
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
            <p v-if="studyMode" class="study-scope-note subtle-note">历史研学保留原文引用和资料问答；本场景请使用下方研学调整入口，普通旅行的仓颉约束与预订补录暂不用于研学。</p>
            <div class="day-tabs" role="tablist" aria-label="选择行程日期"><button v-for="(d, i) in plan.itinerary.days" :key="d.date" role="tab" :aria-selected="activeDay === i" :class="{ active: activeDay === i }" @click="setDay(i)"><span>DAY {{ String(i + 1).padStart(2, '0') }}</span><strong>{{ d.date.slice(5).replace('-', ' / ') }}</strong></button></div>
            <WeatherCard v-for="item in dayWeather" :key="item.city" :weather="item.weather" :city="item.city" :date="day.date" :retrying="weatherRetryCity === item.city" :retry-disabled="operationBusy || checking || Boolean(weatherRetryCity)" :refreshed="Boolean(weatherOverrides[item.city])" :retry-error="weatherRetryErrors[item.city]" @retry="retryWeather(item.city)" />
            <div class="journey-layout">
              <div v-timeline-enter="activeDay" class="day-itinerary card"><div class="day-title"><span class="icon-tile"><Route :size="19" /></span><div><h3>{{ day.title }}</h3><p><template v-if="!studyMode">{{ dayCities.join(' → ') || '跨城移动日' }} · {{ day.stops.length }} 个活动 · 逐段交通以时间轴选择为准</template><template v-else>{{ plan.request.mode === 'walking' ? '步行' : '驾车' }} · 沿着资料寻找线索</template></p></div></div>
                <div class="timeline">
                  <template v-for="event in timeline" :key="event.key">
                    <div v-if="event.conflicts?.length" class="timeline-conflict" role="alert"><CircleAlert :size="16" /><div><strong>此处时间安排存在冲突</strong><p v-for="conflict in event.conflicts" :key="conflict">{{ conflict }}</p></div></div>
                    <DailyTransfer v-if="event.kind === 'transfer' && !studyMode" :transfer="event.transfer" :index="event.transferIndex" :people="plan.request.people" :continued-from-previous="event.continuedFromPrevious" :continues-next-day="event.continuesNextDay" />
                    <template v-else-if="event.kind === 'stop'">
                      <ItineraryStop :stop="event.stop" :index="event.stopIndex" :selected="selectedStopId === event.stop.id" :context="plan.study_context" :expenses="day.expenses.filter(cost => cost.stop_id === event.stop.id && cost.category !== 'transport')" :people="plan.request.people" @select="selectStop" />
                      <p v-if="!studyMode && event.stopIndex < day.stops.length - 1 && routeAfter(event.stop.id)?.scope === 'intercity'" class="transfer">跨城交通已列入本日时间轴，不套用市内导航</p>
                      <RouteChoices v-else-if="!studyMode && event.stopIndex < day.stops.length - 1 && routeAfter(event.stop.id)" :route="routeAfter(event.stop.id)!" :people="plan.request.people" :queryable="canQueryRoute(routeAfter(event.stop.id))" :disabled="operationBusy || checking" @select="chooseRoute(routeAfter(event.stop.id)!, $event)" />
                      <div v-else-if="event.stopIndex < day.stops.length - 1" class="transfer"><component :is="plan.request.mode === 'walking' ? Footprints : CarFront" :size="13" /><span v-if="routeAfter(event.stop.id)?.available">{{ Math.ceil((routeAfter(event.stop.id)!.duration_s || 0) / 60) }} 分钟 · {{ ((routeAfter(event.stop.id)!.distance_m || 0) / 1000).toFixed(1) }} km</span><span v-else>相邻活动间路线暂不可用</span></div>
                    </template>
                  </template>
                </div>
                <div v-if="day.meal_note" class="accommodation meal-advice"><Coffee :size="17" /><div><strong>用餐提醒</strong><p>{{ day.meal_note }}</p></div></div>
                <div v-if="day.accommodation" class="accommodation"><BedDouble :size="17" /><div><strong>今晚住哪里</strong><p>{{ day.accommodation }}</p><small>住宿建议与价格需要自行核实</small></div></div>
                <div v-if="!studyMode && travelTips.days[activeDay]?.length" class="accommodation day-reminders"><Leaf :size="17" /><div><strong>今天的旅行提醒</strong><p v-for="tip in travelTips.days[activeDay]" :key="tip">{{ tip }}</p><small>开放、预约与季节活动请核对官方信息。</small></div></div>
              </div>
              <div class="journey-sidebar"><div class="map-panel card"><div class="map-heading"><div><MapPin :size="17" /><strong>把行程放到地图上</strong></div><span>点击标记查看地点</span></div><div v-if="dayCities.length > 1" class="map-city-tabs" role="group" aria-label="选择地图城市"><button :class="{ active: !mapCity }" @click="mapCity = ''">当天全部</button><button v-for="city in dayCities" :key="city" :class="{ active: mapCity === city }" @click="mapCity = city">{{ city }}</button></div><RouteMap :cities="mapCities" :stops="mapStops" :routes="mapRoutes" :selected-id="selectedStopId" :busy="operationBusy || checking" :can-refresh="!studyMode && mapStops.some(stop => stop.location_behavior !== 'unlocated')" :can-revise="mapStops.some(stop => stop.location_behavior === 'unlocated')" @select="selectStop" @retry="run(false, true)" @revise="reviseProblems" /><div class="map-stats"><div><Route :size="16" /><span>{{ hasMeasuredRoutes ? totalDistance : '—' }} <small>km</small></span><label>已查到的路线总长</label></div><div><Clock3 :size="16" /><span>{{ hasMeasuredRoutes ? totalMinutes : '—' }} <small>分钟</small></span><label>已查到的通行时长</label></div></div><p class="map-footnote">仅展示高德返回的真实路线；缺失路线不会用直线代替。路况变化会影响实际耗时。</p></div>
              <PlaceGuide :plan-id="plan.id" :stop="day.stops.find(stop => stop.id === selectedStopId)" :stops="day.stops" :date="day.date" :disabled="operationBusy || checking" :study-context="plan.study_context" @select="selectedStopId = $event" /></div>
            </div>

            <section class="plan-overview" aria-label="预算与行程管理">
              <div class="budget-card card"><div class="mini-heading"><Wallet :size="18" /><h3>旅行预算</h3><span>全员 · 全程估算</span></div><div class="budget-amount"><strong>¥{{ money(report!.total_cents) }}</strong><span>/ ¥{{ money(report!.budget_cents) }}</span></div><div class="budget-bar"><span :style="{ width: budgetRatio + '%' }" :class="{ over: report!.total_cents > report!.budget_cents }"></span></div><div class="cost-list"><div v-for="cost in costs" :key="cost.category"><span>{{ cost.name }}</span><strong>¥{{ money(cost.value) }}</strong></div></div><p v-if="report!.total_cents > report!.budget_cents" class="budget-excess">当前方案超出预算 ¥{{ money(report!.total_cents - report!.budget_cents) }}，尚未满足预算要求。</p><p v-if="!studyMode && savedTransportCoverage && !savedTransportCoverage.complete" class="budget-excess">跨城交通尚有缺项，当前合计不代表完整旅费。</p><details class="budget-notes"><summary>预算与交通说明</summary><p v-if="!studyMode && plan.request.origin" class="subtle-note">本版本交通范围：{{ transportScopeText(plan.request) }}</p><p class="subtle-note">未核实费用为估算；已订金额以用户填写的订单为准，系统未查验订单，不代表实时报价。</p></details><BudgetDetails :itinerary="plan.itinerary" :people="plan.request.people" /><div class="budget-actions"><span>当前版本 {{ plan.revision }}</span><button type="button" :disabled="operationBusy || checking" @click="deleteHistory"><Trash2 :size="15" />删除此版本</button></div></div>
              <div class="plan-support">
                <PreparationCard v-if="!studyMode" :plan="plan" :disabled="operationBusy || checking" @edit="bookingPanel?.open()" @jump="jumpToIssue" />
                <ConstraintReplan v-if="!studyMode" ref="adjustmentPanel" :plan="plan" :disabled="busy || checking || bookingBusy" @busy="constraintBusy = $event" @updated="acceptConstraintPlan" @refresh="run(false, true)" />
                <details class="reminder-center card">
              <summary><CircleAlert :size="16" /><strong>提醒与检查</strong><span>{{ reminderCount }} 项</span><ChevronDown :size="17" class="reminder-chevron" /></summary>
              <ValidationCard :key="plan.id" :report="plan.validation" :days="plan.itinerary.days" :busy="operationBusy" :checking="checking" @revalidate="validateAgain" @revise="reviseProblems" />
              <PlanRecovery :plan="plan" :busy="operationBusy" :checking="checking" @refresh="run(false, true)" @revise="reviseProblems" />
              <div v-if="!studyMode && travelTips.general.length" class="travel-tips trip-reminders"><Leaf :size="17" /><div><strong>全程与待归属提醒</strong><p v-for="tip in travelTips.general" :key="tip">{{ tip }}</p></div></div>
              <div v-if="plan.adjustments?.length" class="travel-tips"><Clock3 :size="17" /><div><strong>时间调整记录</strong><p v-for="item in plan.adjustments" :key="item">{{ item }}</p></div></div>
            </details>
                <button v-if="!studyMode && form.guides?.length" class="guide-replan secondary-button" :disabled="operationBusy || checking" @click="followGuides"><Landmark :size="16" />按攻略优先重新规划</button>
              </div>
            </section>
            <BookingEditor v-if="!studyMode" ref="bookingPanel" :plan="plan" hide-entry :disabled="busy || checking || constraintBusy" @busy="bookingBusy = $event" @applied="acceptConstraintPlan" />
            <GuideEvidence v-if="!studyMode" :guides="plan.request.guides || []" :decisions="plan.itinerary.guide_decisions || []" :stops="plan.itinerary.days.flatMap(d => d.stops)" />
            <div v-if="studyMode" class="revise-card card"><div class="mini-heading"><Sparkles :size="19" /><h3>{{ studyMode ? '调整这次研学安排' : '让它更像你的旅行' }}</h3><span>每次调整保留旧版本</span></div><p>{{ studyMode ? '改变可用时间、保留喜欢的地点，或调整讲解重点。问历史知识请使用上方资料问答。' : '想少走一点、换个景点，或让第二天更轻松？' }}</p><label v-if="!studyMode" class="field"><span>调整范围</span><select v-model="revisionScope" :disabled="busy"><option value="all">整个行程</option><option value="day">仅当前第 {{ activeDay + 1 }} 天</option></select></label><form class="revision-input" @submit.prevent="run(true)"><input v-model="revision" maxlength="2000" :disabled="busy" :placeholder="studyMode ? '例如：缩短到两小时，保留新渔阳里' : '例如：第二天少安排一个景点，多留些休息时间'" aria-label="行程修改要求" /><button type="submit" :disabled="busy || !revision.trim()" aria-label="提交行程修改"><Send :size="18" /></button></form></div>
            <div v-if="plan.itinerary.guide_insights?.length" class="travel-tips"><Landmark :size="17" /><div><strong>攻略参考摘要（经验信息，未经官方核实）</strong><article v-for="(insight, i) in plan.itinerary.guide_insights" :key="i"><p>{{ insight.text }}</p><a v-if="plan.request.guides?.[insight.source_index]?.url" :href="plan.request.guides[insight.source_index]!.url" target="_blank" rel="noopener noreferrer">{{ plan.request.guides[insight.source_index]!.title }}</a><small v-else>{{ plan.request.guides?.[insight.source_index]?.title }}</small></article></div></div>
            <div v-if="studyMode && plan.itinerary.tips.length" class="travel-tips"><Leaf :size="17" /><div><strong>一点旅行小提醒</strong><p v-for="tip in plan.itinerary.tips" :key="tip">{{ tip }}</p></div></div>
          </template>

          <div v-else-if="!busy" class="empty-workspace card">
            <div class="empty-heading"><span class="eyebrow">A JOURNEY WAITING TO HAPPEN</span><span class="outline-tag"><Compass :size="14" /> 专属于你的行程</span></div>
            <div class="empty-art"><div class="art-ring ring-one"></div><div class="art-ring ring-two"></div><div class="art-compass"><Compass :size="58" stroke-width="1.1" /></div><span class="art-chip chip-one"><Coffee :size="16" /> 一杯本地咖啡</span><span class="art-chip chip-two"><Landmark :size="16" /> 一段城市故事</span><span class="art-chip chip-three"><Leaf :size="16" /> 一点留白的时间</span></div>
            <h3>{{ studyMode ? '让一段历史，有迹可循。' : '旅程，从你的期待开始。' }}</h3><p v-if="studyMode">提出一个学习问题，从资料里寻找人物与地点，<br />生成带出处的讲解、现场任务与真实行走路线。</p><p v-else>填写左侧的旅行偏好，我们会为你整理每日行程，<br />计算真实路线，并检查时间与预算是否合适。</p>
            <div class="empty-steps"><div><span>01</span><strong>说说你的期待</strong><small>目的地、时间与偏好</small></div><ArrowRight :size="16" /><div><span>02</span><strong>看见旅行轮廓</strong><small>每日行程与真实路线</small></div><ArrowRight :size="16" /><div><span>03</span><strong>调整成你的节奏</strong><small>校验问题，一次次完善</small></div></div>
            <div class="empty-bottom"><CircleCheck :size="14" /> 不需要下载大模型或 PDF 权重，就能开始规划。</div>
          </div>
          <details v-if="tools.length" class="tool-log card"><summary><Route :size="16" /> 查看本次查询记录 <span>{{ tools.length }} 次工具调用</span></summary><div class="tool-log-body"><article v-for="(tool, i) in tools" :key="i"><strong>{{ tool.name }}</strong><code>{{ JSON.stringify(tool.arguments) }}</code><p>{{ tool.summary }}</p></article></div></details>
          <p v-if="progress && !busy && !error" class="completion-note"><Check :size="14" /> {{ progress }}</p>
        </section>
      </div>
    </main>
    <footer><a class="footer-brand" href="#"><Compass :size="17" /> 知途 · ZHITU</a><p>认真规划，自在出发。</p><button class="motion-control" type="button" :aria-pressed="pageMotion" :disabled="reducedMotion" @click="toggleMotion"><Pause v-if="pageMotion" :size="13" /><Play v-else :size="13" />{{ reducedMotion ? '系统已减少动态效果' : pageMotion ? '动态效果：开' : '动态效果：关' }}</button><a href="https://lbs.amap.com/" target="_blank" rel="noopener noreferrer">地图与路线由高德提供 <ExternalLink :size="12" /></a></footer>

    <Transition name="appearance-modal">
    <div v-if="historyOpen" class="modal-backdrop" @click.self="historyOpen = false"><section class="history-modal card" role="dialog" aria-modal="true" aria-labelledby="history-title"><div class="mini-heading"><History :size="20" /><h3 id="history-title">我的行程</h3><button class="icon-button" aria-label="关闭行程历史" @click="historyOpen = false"><X :size="20" /></button></div><p v-if="!history.length" class="history-empty">还没有保存的行程。完成第一次规划后，会自动出现在这里。</p><button v-for="item in history" :key="item.id" class="history-item" :disabled="operationBusy" @click="openHistory(item.id)"><span class="icon-tile"><MapPin :size="20" /></span><div><strong>{{ item.title }}</strong><span>{{ item.destination }} · {{ item.start_date }} · 版本 {{ item.revision }}</span></div><ChevronRight :size="18" /></button></section></div>
    </Transition>
  </div>
</template>

<style scoped>
.budget-excess { color: #a65a32; font-size: 12px; line-height: 1.7; margin-top: 14px; }
.intercity-preferences { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.intercity-preferences label { display:flex; align-items:center; gap:7px; color:#416657; font-size:12px; line-height:1.7; }
.intercity-preferences input { accent-color:#117b71; flex-shrink:0; }
.hotel-input { margin:16px 0; }
.hotel-input summary { cursor:pointer; color:#335b4d; font-size:13px; font-weight:600; }
.hotel-input .subtle-note { margin:10px 0; }
.plan-overview { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 18px; margin: 24px 0 18px; align-items: start; }
.plan-overview > .budget-card { min-width: 0; padding: 24px; }
.plan-overview .mini-heading h3 { font-size: 16px; }
.plan-overview .mini-heading > span { font-size: 11px; }
.plan-overview .budget-amount { margin: 23px 0 16px; }
.plan-overview .budget-amount strong { font-size: 36px; }
.plan-overview .budget-amount > span { font-size: 13px; }
.plan-overview .cost-list { gap: 0; margin: 20px 0; }
.plan-overview .cost-list > div { padding: 10px 0; font-size: 14px; border-bottom: 1px solid #eff2eb; gap: 14px; }
.plan-overview .cost-list > div:last-child { border-bottom: 0; }
.plan-overview .cost-list strong { font-variant-numeric: tabular-nums; white-space: nowrap; }
.budget-notes { color: #6b806c; font-size: 13px; line-height: 1.8; margin-top: 17px; }
.budget-notes summary { cursor: pointer; color: #52755e; }
.budget-notes .subtle-note { font-size: 13px; line-height: 1.8; margin-top: 10px; }
.plan-overview :deep(.budget-details) { margin-top: 13px; padding-top: 13px; font-size: 13px; }
.budget-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-top: 22px; padding-top: 16px; border-top: 1px solid #e6ece2; }
.budget-actions > span { color: #7e8f7d; font-size: 12px; }
.budget-actions button { display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; border: 1px solid #eaded2; border-radius: 8px; color: #977158; background: #fffaf5; font-size: 12px; }
.budget-actions button:hover:not(:disabled) { background: #fbefe5; border-color: #d6bca7; }
.budget-actions button:focus-visible, .reminder-center > summary:focus-visible { outline: 2px solid #80a68a; outline-offset: 3px; }
.plan-support { display: grid; gap: 16px; min-width: 0; }
.plan-support :deep(.preparation-card), .plan-support :deep(.constraint-entry) { margin: 0; }
.plan-support :deep(.preparation-card) { padding: 21px; }
.plan-support :deep(.preparation-grid) { grid-template-columns: 1fr; }
.reminder-center { margin: 0; padding: 0; overflow: hidden; min-width: 0; }
.reminder-center > summary { display: flex; align-items: center; gap: 9px; padding: 21px; cursor: pointer; color: #315843; list-style: none; }
.reminder-center > summary::-webkit-details-marker { display: none; }
.reminder-center > summary strong { font-size: 16px; }
.reminder-center > summary span { padding: 3px 9px; border-radius: 7px; background: #f4efe4; color: #8a6c42; font-size: 12px; }
.reminder-chevron { margin-left: auto; color: #7a947c; transition: transform .15s; }
.reminder-center[open] > summary { border-bottom: 1px solid #e8eee2; }
.reminder-center[open] .reminder-chevron { transform: rotate(180deg); }
.reminder-center :deep(.validation-card) { box-shadow: none; border: 0; border-radius: 0; padding: 18px 21px; margin: 0; }
.reminder-center :deep(.travel-tips) { margin: 0; padding: 16px 21px; border-top: 1px solid #eef2e8; }
.reminder-center :deep(.travel-tips p) { font-size: 13px; line-height: 1.85; }
.guide-replan { justify-self: start; }
.plan-overview:has(.constraint-panel) .plan-support { display: contents; }
.plan-overview:has(.constraint-panel) > .budget-card { grid-column: 1; grid-row: 1 / span 2; }
.plan-overview:has(.constraint-panel) :deep(.preparation-card) { grid-column: 2; grid-row: 1; }
.plan-overview:has(.constraint-panel) .reminder-center { grid-column: 2; grid-row: 2; }
.plan-overview:has(.constraint-panel) :deep(.constraint-entry) { grid-column: 1 / -1; grid-row: 3; }
@media(max-width:1000px) {
  .plan-overview { grid-template-columns: minmax(0, 1fr); }
  .plan-overview:has(.constraint-panel) > .budget-card, .plan-overview:has(.constraint-panel) :deep(.preparation-card), .plan-overview:has(.constraint-panel) .reminder-center, .plan-overview:has(.constraint-panel) :deep(.constraint-entry) { grid-column: 1; grid-row: auto; }
}
@media(max-width:520px) {
  .plan-overview > .budget-card { padding: 19px; }
  .plan-support :deep(.preparation-card), .reminder-center > summary { padding: 18px; }
  .plan-overview .budget-amount strong { font-size: 32px; }
}
@media(prefers-reduced-motion:reduce) { .reminder-chevron { transition: none; } }
.day-reminders p { margin:6px 0; }
.study-scope-note { padding:0 4px; }
.timeline-conflict { display:flex; align-items:flex-start; gap:8px; padding:12px 14px; margin:12px 0 8px; border:1px solid #e8c6aa; border-radius:10px; background:#fff7ed; color:#965c31; overflow-wrap:anywhere; }
.timeline-conflict > svg { flex-shrink:0; margin-top:2px; }
.timeline-conflict strong { font-size:12px; }
.timeline-conflict p { margin:5px 0 0; font-size:12px; line-height:1.7; }
</style>
