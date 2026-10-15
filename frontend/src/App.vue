<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { Compass, ArrowUpRight, ArrowRight, MapPin, CalendarDays, Users, Wallet, Footprints, CarFront,
  Sparkles, Clock3, ChevronRight, Check, CircleCheck, CircleAlert, RefreshCw, X, History, SlidersHorizontal,
  Coffee, BedDouble, Landmark, Send, Route, LoaderCircle, Leaf, ExternalLink } from 'lucide-vue-next'
import CityScene from './components/CityScene.vue'
import RouteMap from './components/RouteMap.vue'
import { api, stream } from './api'
import type { TripRequest, Plan, Health, HistoryItem, ToolEvent, Stop, ValidationReport } from './types'

function localDate() {
  const d = new Date(); d.setDate(d.getDate() + 7)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const form = reactive<TripRequest>({ origin: '', destination: '上海', start_date: localDate(), start_time: '09:00',
  days: 3, style: 'balanced', budget: 3000, people: 2, mode: 'walking', preferences: '' })
const health = ref<Health | null>(null), plan = ref<Plan | null>(null), history = ref<HistoryItem[]>([])
const historyOpen = ref(false), busy = ref(false), progress = ref(''), error = ref(''), tools = ref<ToolEvent[]>([])
const activeDay = ref(0), selectedStopId = ref(''), revision = ref(''), checking = ref(false), showUnknowns = ref(false)
const resultSection = ref<HTMLElement>()
let controller: AbortController | null = null
const day = computed(() => plan.value?.itinerary.days[activeDay.value])
const dayRoutes = computed(() => plan.value?.routes.filter(r => r.day_index === activeDay.value) || [])
const report = computed(() => plan.value?.validation)
const issues = computed(() => [...(report.value?.errors || []), ...(report.value?.warnings || [])])
const totalMinutes = computed(() => Math.ceil(dayRoutes.value.reduce((sum, r) => sum + (r.duration_s || 0), 0) / 60))
const totalDistance = computed(() => (dayRoutes.value.reduce((sum, r) => sum + (r.distance_m || 0), 0) / 1000).toFixed(1))
const categoryNames: Record<string, string> = { ticket: '景点门票', food: '餐饮', hotel: '住宿', transport: '市内交通', other: '其他' }
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
  selectedStopId.value = plan.value?.itinerary.days[index]?.stops[0]?.id || ''
}
function selectStop(id: string) {
  selectedStopId.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}
async function openHistory(id: string) {
  if (busy.value) return
  try {
    plan.value = await api<Plan>(`/api/plans/${id}`)
    plan.value.validation = await api<ValidationReport>(`/api/plans/${id}/validate`, { method: 'POST' })
    Object.assign(form, plan.value.request)
    setDay(0); historyOpen.value = false; error.value = ''; tools.value = []
  } catch (e) { error.value = e instanceof Error ? e.message : '读取失败' }
}

async function run(isRevision = false) {
  if (busy.value || (isRevision && (!plan.value || !revision.value.trim()))) return
  busy.value = true; error.value = ''; tools.value = []; progress.value = '准备开始规划…'
  controller = new AbortController()
  try {
    await nextTick(); resultSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    await stream(isRevision ? `/api/plans/${plan.value!.id}/revise` : '/api/plans/generate',
      isRevision ? { instruction: revision.value } : { ...form, days: Number(form.days), people: Number(form.people), budget: Number(form.budget) },
      controller.signal, (kind, data) => {
        if (kind === 'status') progress.value = (data as { message: string }).message
        if (kind === 'tool') tools.value.push(data as ToolEvent)
        if (kind === 'result') { plan.value = data as Plan; setDay(0); revision.value = ''; progress.value = '行程已生成并完成校验' }
      })
    await refreshHistory()
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') progress.value = '本次规划已取消，已有方案仍然保留'
    else error.value = e instanceof Error ? e.message : '规划失败，请重试'
  } finally { busy.value = false; controller = null }
}
async function validateAgain() {
  if (!plan.value || checking.value) return
  checking.value = true
  try { plan.value.validation = await api<ValidationReport>(`/api/plans/${plan.value.id}/validate`, { method: 'POST' }) }
  catch (e) { error.value = e instanceof Error ? e.message : '校验失败' }
  finally { checking.value = false }
}
function reviseProblems() {
  revision.value = '请修正当前校验发现的问题，保留我的偏好。重新安排时间以满足真实路线时长，避免重复景点，并让全员估算费用不超过预算。'
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
        <a href="#planner" class="hero-link">开始我的城市漫游 <ArrowUpRight :size="20" /></a>
        <div class="hero-features"><span><Route :size="15" /> 真实地图路线</span><span><Wallet :size="15" /> 全员预算核对</span><span><SlidersHorizontal :size="15" /> 随心调整行程</span></div>
      </div>
      <CityScene />
    </section>

    <main id="planner" class="planner">
      <div class="section-heading"><div><span class="eyebrow">MAKE IT YOURS</span><h2>{{ plan ? '你的旅程，有了轮廓。' : '下一站，你想去哪里？' }}</h2></div><span class="section-meta">城市旅行规划工作台 <span>01 — 03</span></span></div>
      <div class="planner-grid">
        <aside class="request-panel card">
          <div class="panel-heading"><div class="icon-tile"><SlidersHorizontal :size="19" /></div><div><h3>旅行偏好</h3><p>从几个小小的期待开始</p></div></div>
          <form @submit.prevent="run()">
            <fieldset :disabled="busy">
              <label class="field"><span>目的地城市 <b>*</b></span><div class="input-wrap"><MapPin :size="17" /><input v-model="form.destination" required maxlength="80" placeholder="例如：上海、杭州" /></div></label>
              <label class="field"><span>出发地 <small>可选，仅作背景</small></span><div class="input-wrap"><Compass :size="17" /><input v-model="form.origin" maxlength="80" placeholder="你从哪里出发？" /></div></label>
              <div class="field-row"><label class="field"><span>出行日期</span><div class="input-wrap"><input v-model="form.start_date" type="date" required aria-label="出行日期" /></div></label><label class="field time-field"><span>首日开始</span><div class="input-wrap"><input v-model="form.start_time" type="time" required aria-label="首日开始时间" /></div></label></div>
              <div class="field-row"><label class="field"><span>旅行天数</span><div class="input-wrap"><CalendarDays :size="16" /><input v-model.number="form.days" type="number" min="1" max="10" required /><span class="input-unit">天</span></div></label><label class="field"><span>同行人数</span><div class="input-wrap"><Users :size="16" /><input v-model.number="form.people" type="number" min="1" max="50" required /><span class="input-unit">人</span></div></label></div>
              <label class="field"><span>全员总预算 <small>目的地期间</small></span><div class="input-wrap"><Wallet :size="17" /><input v-model.number="form.budget" type="number" min="1" max="10000000" step="0.01" required /><span class="input-unit">元</span></div></label>
              <div class="field"><span>旅行节奏</span><div class="segmented"><button type="button" :class="{ selected: form.style === 'relaxed' }" @click="form.style = 'relaxed'">慢慢逛</button><button type="button" :class="{ selected: form.style === 'balanced' }" @click="form.style = 'balanced'">刚刚好</button><button type="button" :class="{ selected: form.style === 'compact' }" @click="form.style = 'compact'">多看些</button></div></div>
              <div class="field"><span>市内出行方式</span><div class="transport-options"><button type="button" :class="{ selected: form.mode === 'walking' }" @click="form.mode = 'walking'"><Footprints :size="19" /><span>步行探索</span><Check v-if="form.mode === 'walking'" :size="15" /></button><button type="button" :class="{ selected: form.mode === 'driving' }" @click="form.mode = 'driving'"><CarFront :size="19" /><span>驾车出行</span><Check v-if="form.mode === 'driving'" :size="15" /></button></div></div>
              <label class="field preferences-field"><span>还有什么小期待？</span><textarea v-model="form.preferences" rows="3" maxlength="2000" placeholder="想看建筑、尝本地美食，或带孩子轻松逛逛…"></textarea></label>
              <button class="primary-button generate-button" type="submit" :disabled="!health?.capabilities.planning"><LoaderCircle v-if="busy" :size="18" class="spin" /><Sparkles v-else :size="18" />{{ busy ? '正在规划旅程' : plan ? '重新规划一份行程' : '生成我的专属行程' }}<ArrowRight v-if="!busy" :size="17" /></button>
            </fieldset>
          </form>
          <p class="form-note">预算包含全员的市内游玩、餐饮与住宿估算，跨城车票和机票暂不包含。</p>
          <div v-if="health?.missing.length" class="config-notice"><CircleAlert :size="16" /><span>待配置：{{ health.missing.join('、') }}</span></div>
        </aside>

        <section ref="resultSection" class="workspace" aria-live="polite">
          <div v-if="error" class="error-banner" role="alert"><CircleAlert :size="19" /><div><strong>这次没能顺利完成</strong><p>{{ error }}</p></div><button aria-label="关闭错误提示" @click="error = ''"><X :size="17" /></button></div>
          <div v-if="busy" class="progress-card card"><div class="progress-icon"><Compass :size="26" class="spin-slow" /></div><div><span class="eyebrow">A LITTLE PLANNING, A LOT TO DISCOVER</span><h3>{{ progress }}</h3><p>正在结合地点、路线和你的旅行偏好。通常需要几十秒。</p></div><button class="text-button" @click="controller?.abort()">取消</button><div class="progress-line"></div></div>

          <template v-if="plan && day">
            <div class="plan-heading card"><div><span class="eyebrow"><MapPin :size="13" /> {{ plan.request.destination }} · {{ plan.request.days }} 天 · {{ plan.request.people }} 人</span><h2>{{ plan.itinerary.title }}</h2><p>{{ plan.itinerary.summary }}</p></div><span class="revision-badge">版本 {{ plan.revision }}</span></div>
            <div class="day-tabs" role="tablist" aria-label="选择行程日期"><button v-for="(d, i) in plan.itinerary.days" :key="d.date" role="tab" :aria-selected="activeDay === i" :class="{ active: activeDay === i }" @click="setDay(i)"><span>DAY {{ String(i + 1).padStart(2, '0') }}</span><strong>{{ d.date.slice(5).replace('-', ' / ') }}</strong></button></div>
            <div class="journey-layout">
              <div class="day-itinerary card"><div class="day-title"><span class="icon-tile"><Route :size="19" /></span><div><h3>{{ day.title }}</h3><p>{{ day.stops.length }} 个活动 · {{ plan.request.mode === 'walking' ? '步行' : '驾车' }}</p></div></div>
                <div class="timeline">
                  <template v-for="(stop, i) in day.stops" :key="stop.id">
                    <button :id="stop.id" class="stop-card" :class="{ active: selectedStopId === stop.id }" @click="selectStop(stop.id)">
                      <span class="stop-index">{{ String(i + 1).padStart(2, '0') }}</span><div class="stop-body"><div class="stop-meta"><span>{{ stop.start }} — {{ stop.end }}</span><span class="kind-tag"><component :is="stopIcon(stop)" :size="12" />{{ kindLabel(stop.kind) }}</span></div><h4>{{ stop.name }}</h4><p>{{ stop.description }}</p><span class="poi-note" :class="{ unknown: stop.location_status !== 'verified' }"><MapPin :size="12" />{{ stop.location_status === 'verified' ? (stop.matched_name || stop.name) : stop.kind === 'break' ? '自由活动' : '地点待核对' }}</span><span v-if="stop.address && selectedStopId === stop.id" class="stop-address">{{ stop.address }}</span></div><ChevronRight :size="15" class="stop-arrow" />
                    </button>
                    <div v-if="i < day.stops.length - 1" class="transfer"><component :is="plan.request.mode === 'walking' ? Footprints : CarFront" :size="13" /><span v-if="dayRoutes[i]?.available">{{ Math.ceil((dayRoutes[i]!.duration_s || 0) / 60) }} 分钟 · {{ ((dayRoutes[i]!.distance_m || 0) / 1000).toFixed(1) }} km</span><span v-else>路线暂不可用</span></div>
                  </template>
                </div>
                <div v-if="day.accommodation" class="accommodation"><BedDouble :size="17" /><div><strong>今晚住哪里</strong><p>{{ day.accommodation }}</p><small>住宿建议与价格需要自行核实</small></div></div>
              </div>
              <div class="map-panel card"><div class="map-heading"><div><MapPin :size="17" /><strong>把行程放到地图上</strong></div><span>点击标记查看地点</span></div><RouteMap :stops="day.stops" :routes="dayRoutes" :selected-id="selectedStopId" @select="selectStop" /><div class="map-stats"><div><Route :size="16" /><span>{{ totalDistance }} <small>km</small></span><label>已查到的路线总长</label></div><div><Clock3 :size="16" /><span>{{ totalMinutes }} <small>分钟</small></span><label>已查到的通行时长</label></div></div><p class="map-footnote">仅展示高德返回的真实路线；缺失路线不会用直线代替。路况变化会影响实际耗时。</p></div>
            </div>

            <div class="insights-grid">
              <div class="budget-card card"><div class="mini-heading"><Wallet :size="18" /><h3>旅行预算</h3><span>全员 · 全程估算</span></div><div class="budget-amount"><strong>¥{{ money(report!.total_cents) }}</strong><span>/ ¥{{ money(report!.budget_cents) }}</span></div><div class="budget-bar"><span :style="{ width: budgetRatio + '%' }" :class="{ over: report!.total_cents > report!.budget_cents }"></span></div><div class="cost-list"><div v-for="cost in costs" :key="cost.category"><span>{{ cost.name }}</span><strong>¥{{ money(cost.value) }}</strong></div></div><p class="subtle-note">所有费用均为估算，不代表实时票价或酒店报价。</p></div>
              <div class="validation-card card"><div class="mini-heading"><CircleCheck :size="18" /><h3>出发前，检查一下</h3><button class="icon-button" :disabled="checking || busy" aria-label="重新校验方案" @click="validateAgain"><RefreshCw :size="15" :class="{ spin: checking }" /></button></div><div class="validation-status" :class="{ 'has-issues': issues.length }"><component :is="issues.length ? CircleAlert : CircleCheck" :size="21" /><div><strong>{{ issues.length ? `${issues.length} 项安排值得调整` : '已知的时间与预算检查通过' }}</strong><p>{{ report!.unknowns.length ? `还有 ${report!.unknowns.length} 项信息需要你确认` : '所有已知检查通过' }}</p></div></div><ul v-if="issues.length" class="issues-list"><li v-for="(issue, i) in issues" :key="i"><span v-if="issue.day_index !== null">第 {{ issue.day_index + 1 }} 天：</span>{{ issue.message }}</li></ul><button v-if="issues.length" class="secondary-button" :disabled="busy" @click="reviseProblems"><Sparkles :size="15" /> 请 AI 调整这些问题</button><button class="unknown-toggle" @click="showUnknowns = !showUnknowns">{{ showUnknowns ? '收起' : '查看' }}待确认信息 <ChevronRight :size="14" :class="{ rotated: showUnknowns }" /></button><ul v-if="showUnknowns" class="unknown-list"><li v-for="(item, i) in report!.unknowns" :key="i">{{ item.day_index !== null ? `第 ${item.day_index + 1} 天：` : '' }}{{ item.message }}</li></ul></div>
            </div>
            <div class="revise-card card"><div class="mini-heading"><Sparkles :size="19" /><h3>让它更像你的旅行</h3><span>每次调整保留旧版本</span></div><p>想少走一点、换个景点，或让第二天更轻松？</p><form class="revision-input" @submit.prevent="run(true)"><input v-model="revision" maxlength="2000" :disabled="busy" placeholder="例如：第二天少安排一个景点，多留些休息时间" aria-label="行程修改要求" /><button type="submit" :disabled="busy || !revision.trim()" aria-label="提交行程修改"><Send :size="18" /></button></form></div>
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
