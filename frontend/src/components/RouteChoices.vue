<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown } from 'lucide-vue-next'
import type { Mode, RouteLeg } from '../types'
import { groupRange, localRecommendation, modeNames, type LocalOption } from '../localTransport'
import { routeChoiceAction } from '../routeChoiceActions'

const props = withDefaults(defineProps<{ route: RouteLeg; disabled: boolean; people?: number; queryable?: boolean }>(), { people: 1, queryable: false })
const emit = defineEmits<{ select: [mode: Mode] }>()
const options = computed(() => {
  const byMode = new Map<Mode, LocalOption>((props.route.alternatives || []).map(option => [option.mode, option]))
  byMode.set(props.route.mode, props.route)
  return byMode
})
const modes = ['walking', 'transit', 'taxi', 'driving', 'bicycling'] as const
const rows = computed(() => modes.map(mode => ({ mode, option: options.value.get(mode) })))
const recommendation = computed(() => localRecommendation([...options.value.values()], props.people))
const canQuery = computed(() => props.queryable && props.route.scope === 'local' && !props.route.stationary)
const explanation = computed(() => props.route.selection_reason || recommendation.value.reason)
const chosenCost = computed(() => {
  const amount = cost(props.route)
  return amount === '待核实' ? '全员费用待核实' : `全员约 ${amount}（${props.people}人）`
})
function action(mode: Mode, option?: LocalOption) {
  return routeChoiceAction(props.route, mode, Boolean(option?.available), canQuery.value, props.disabled)
}
function actionTitle(mode: Mode, option?: LocalOption) {
  if (props.disabled) return '请等待当前操作完成'
  if (props.route.mode === mode && option?.available) return '当前采用的交通方式'
  if (option?.available) return '预览对时间和预算的影响，确认后采用'
  return canQuery.value ? '查询真实路线并预览，确认后采用' : '需先核实两端地点，才可查询路线'
}
function duration(option?: LocalOption) {
  return !option?.available ? '待查询' : option.duration_s == null ? '待核实' : `${Math.ceil(option.duration_s / 60)} 分钟`
}
function distance(meters: number | null) {
  if (meters == null) return '里程待核实'
  return meters < 1000 ? `${meters} 米` : `${(meters / 1000).toFixed(1)} 公里`
}
function cost(option: LocalOption) {
  const range = groupRange(option, props.people)
  if (!range) return '待核实'
  const money = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return `¥${money(range[0])}${range[0] === range[1] ? '' : `–${money(range[1])}`}`
}
function notes(option?: LocalOption) {
  if (!option) return ['尚未查询；未取得路线不代表当地没有这种交通。']
  return [...new Set([
    option.instructions?.filter(Boolean).join(' → '), option.note,
    [option.cost_estimate?.method, option.cost_estimate?.assumptions].filter(Boolean).join('；'),
  ].filter((text): text is string => Boolean(text)))]
}
</script>

<template>
  <div class="route-choices">
    <p v-if="route.stationary">原地活动，无需地点间转移</p>
    <template v-else>
      <p class="route-picked">
        <strong>{{ modeNames[route.mode] }}</strong><span v-if="recommendation.mode === route.mode" class="mode-badge">推荐</span>
        <span class="picked-meta">{{ route.available ? duration(route) : '路线未核实' }} · {{ distance(route.distance_m) }} · {{ chosenCost }}</span>
      </p>
      <details class="mode-compare">
        <summary class="compare-toggle">比较交通<ChevronDown :size="14" /></summary>
        <div class="mode-table-wrap">
          <table class="mode-table">
            <caption class="sr-only">地点间交通比较；费用为{{ people }}人的全员参考费用</caption>
            <colgroup><col class="mode-column" /><col class="duration-column" /><col class="cost-column" /><col class="action-column" /></colgroup>
            <thead><tr><th scope="col">方式</th><th scope="col">耗时</th><th scope="col">全员费用<span>（{{ people }}人）</span></th><th scope="col">操作</th></tr></thead>
            <tbody>
              <tr v-for="item in rows" :key="item.mode" :class="{ selected: route.mode === item.mode && item.option?.available }">
                <th scope="row"><div class="mode-name">{{ modeNames[item.mode] }}<span v-if="recommendation.mode === item.mode" class="mode-badge">推荐</span></div></th>
                <td class="mode-duration" :class="{ pending: !item.option?.available }">{{ duration(item.option) }}</td>
                <td class="mode-cost" :class="{ pending: !item.option?.available }">{{ item.option?.available ? cost(item.option) : '—' }}</td>
                <td class="mode-operation"><button type="button" class="mode-action" :class="{ current: route.mode === item.mode && item.option?.available }" :disabled="!action(item.mode, item.option).enabled" :title="actionTitle(item.mode, item.option)" :aria-label="`${modeNames[item.mode]}：${action(item.mode, item.option).label}`" @click="emit('select', item.mode)">{{ action(item.mode, item.option).label }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <details class="route-explanation">
          <summary>费用与路线说明</summary>
          <div class="explanation-body">
            <p v-if="explanation">{{ explanation }}</p>
            <p>费用为参考估算；地图耗时不含额外候车、进出站及停车取车。选择其他方式后先预览，确认后才会应用。</p>
            <section v-for="item in rows" :key="item.mode">
              <h4>{{ modeNames[item.mode] }}</h4>
              <p v-for="note in notes(item.option)" :key="note">{{ note }}</p>
              <p v-if="item.option?.provider_endpoint">路线来源：高德 {{ item.option.provider_endpoint }}</p>
              <a v-if="item.option?.cost_estimate?.source_url" :href="item.option.cost_estimate.source_url" target="_blank" rel="noopener noreferrer">查看费用来源</a>
            </section>
            <p>打车按每车最多4人估算，实际费用以平台为准；自驾需确认车辆与停车条件，骑行需确认车辆、运营时间与体力条件。</p>
          </div>
        </details>
      </details>
    </template>
  </div>
</template>

<style scoped>
.route-choices { margin: 6px 0; min-width: 0; }
.route-picked { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; margin: 0; font-size: 14px; color: #2f4f43; line-height: 1.7; }
.route-picked .picked-meta { color: #5d7267; font-size: 13px; }
.mode-badge { display: inline-block; color: #287768; background: #e1f1e7; border-radius: 5px; padding: 1px 5px; white-space: nowrap; font-size: 11px; font-weight: 500; line-height: 1.7; }
.mode-compare { margin-top: 8px; min-width: 0; }
.compare-toggle { display: inline-flex; align-items: center; gap: 6px; color: #4b6c5c; font-size: 13px; line-height: 1.8; cursor: pointer; list-style: none; }
.compare-toggle::-webkit-details-marker { display: none; }
.compare-toggle svg { transition: transform .15s; }
.mode-compare[open] > .compare-toggle svg { transform: rotate(180deg); }
.mode-table-wrap { margin-top: 10px; border: 1px solid #dce6df; border-radius: 12px; overflow: hidden; background: #fff; }
.mode-table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 13px; color: #4c6858; line-height: 1.65; }
.mode-column { width: 30%; }
.duration-column { width: 20%; }
.cost-column { width: 32%; }
.action-column { width: 18%; }
.mode-table th, .mode-table td { padding: 11px 10px; text-align: left; vertical-align: middle; overflow-wrap: anywhere; }
.mode-table thead th { padding-top: 9px; padding-bottom: 9px; color: #7a8d7b; font-size: 12px; font-weight: 400; background: #f8faf7; }
.mode-table thead th span { display: inline-block; }
.mode-table tbody th { font-weight: 600; color: #355849; }
.mode-table tbody tr + tr { border-top: 1px solid #e9eee8; }
.mode-table tbody tr.selected { background: #edf5ee; }
.mode-name { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 6px; }
.mode-duration, .mode-cost { font-variant-numeric: tabular-nums; }
.mode-table .pending { color: #8a998e; }
.mode-table .mode-operation, .mode-table thead th:last-child { text-align: right; }
.mode-action { padding: 5px 9px; border: 1px solid #cfdfd5; border-radius: 7px; background: #fff; color: #397765; font-size: 12px; line-height: 1.6; white-space: nowrap; }
.mode-action:hover:not(:disabled) { background: #edf5ef; border-color: #8cb6a2; }
.mode-action:disabled { cursor: default; opacity: .5; }
.mode-action.current:disabled { border-color: transparent; background: transparent; color: #39745d; opacity: 1; }
.compare-toggle:focus-visible, .route-explanation summary:focus-visible, .mode-action:focus-visible { outline: 2px solid #7ba58e; outline-offset: 3px; border-radius: 4px; }
.route-explanation { margin-top: 11px; color: #6e8574; font-size: 13px; line-height: 1.8; }
.route-explanation summary { cursor: pointer; width: fit-content; }
.explanation-body { padding: 12px 0 2px; }
.explanation-body p { margin: 0 0 10px; overflow-wrap: anywhere; }
.explanation-body section { border-top: 1px solid #e1e9df; padding-top: 12px; margin-top: 12px; }
.explanation-body h4 { font-size: 13px; color: #486e58; margin: 0 0 6px; }
.explanation-body a { color: #267667; text-decoration: underline; text-underline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@media(max-width:520px) {
  .mode-table th, .mode-table td { padding: 10px 6px; }
  .mode-column { width: 29%; }
  .duration-column { width: 21%; }
  .mode-action { padding: 5px 6px; }
}
@media(prefers-reduced-motion:reduce) { .compare-toggle svg { transition: none; } }
</style>
