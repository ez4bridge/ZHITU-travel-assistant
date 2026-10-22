<script setup lang="ts">
import { computed } from 'vue'
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
const primary = computed(() => (['walking', 'transit', 'taxi'] as Mode[]).map(mode => ({ mode, option: options.value.get(mode) })))
const more = computed(() => (['driving', 'bicycling'] as Mode[]).map(mode => ({ mode, option: options.value.get(mode) })))
const recommendation = computed(() => localRecommendation([...options.value.values()], props.people))
const canQuery = computed(() => props.queryable && props.route.scope === 'local' && !props.route.stationary)
function selectable(mode: Mode, option?: LocalOption) {
  return routeChoiceAction(props.route, mode, Boolean(option?.available), canQuery.value, props.disabled).enabled
}
function actionLabel(mode: Mode, option?: LocalOption) {
  return routeChoiceAction(props.route, mode, Boolean(option?.available), canQuery.value, props.disabled).label
}
const distance = (meters: number | null) => meters == null ? '里程待核实' : meters < 1000 ? `${meters} 米` : `${(meters / 1000).toFixed(1)} 公里`
function cost(option: LocalOption) {
  const range = groupRange(option, props.people)
  if (!range) return '全员费用待核实，不能当免费'
  const money = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return `全员约 ¥${money(range[0])}${range[0] === range[1] ? '' : `–${money(range[1])}`}（${props.people}人）`
}
</script>

<template>
  <div class="route-choices">
    <p v-if="route.stationary">原地活动，无需地点间转移</p>
    <template v-else>
      <p><strong>已选 {{ modeNames[route.mode] }}</strong> · {{ route.available && route.duration_s != null ? `${Math.ceil(route.duration_s / 60)} 分钟` : '路线未核实' }} · {{ distance(route.distance_m) }}</p>
      <p class="subtle-note">{{ route.selection_reason || route.note }}</p>
      <p class="recommendation-note">参考推荐：{{ recommendation.reason }}</p>
      <div class="common-mode-grid">
        <article v-for="item in primary" :key="item.mode" class="common-mode-card" :class="{ selected: route.mode === item.mode }">
          <strong>{{ modeNames[item.mode] }} <span v-if="recommendation.mode === item.mode" class="mode-badge">推荐</span></strong>
          <template v-if="item.option?.available">
            <span>{{ item.option.duration_s == null ? '耗时待核实' : `${Math.ceil(item.option.duration_s / 60)} 分钟` }} · {{ distance(item.option.distance_m) }}</span>
            <span>{{ cost(item.option) }}</span>
            <small>{{ item.option.instructions?.join(' → ') || item.option.note }}</small>
            <details v-if="item.option.cost_estimate || item.option.provider_endpoint">
              <summary>查看数据与费用依据</summary>
              <small v-if="item.option.cost_estimate">{{ item.option.cost_estimate.method }}；{{ item.option.cost_estimate.assumptions }} <a v-if="item.option.cost_estimate.source_url" :href="item.option.cost_estimate.source_url" target="_blank" rel="noopener noreferrer">费用来源</a></small>
              <small v-if="item.option.provider_endpoint">路线来源：高德 {{ item.option.provider_endpoint }}</small>
            </details>
          </template>
          <template v-else><span>暂无可用路线快照</span><small>{{ item.option?.note || '尚未查询此方式，不代表当地没有此交通。' }}</small><small v-if="canQuery">可先查询真实路线；查询成功并通过预览后才能采用。</small><small v-else>需先核实两端具体地点，才可查询此段交通。</small></template>
          <button type="button" class="text-button" :disabled="!selectable(item.mode, item.option)" @click="emit('select', item.mode)">{{ actionLabel(item.mode, item.option) }}</button>
        </article>
      </div>
      <p class="subtle-note">地图路耗不含额外候车、进出站、停车取车；打车费用非实时报价，每车最多4人估算。自行确认体力、无障碍与运营时间。</p>
      <details class="additional-modes"><summary>更多方式：自驾与骑行</summary>
        <div v-for="item in more" :key="item.mode" class="additional-mode">
          <div><strong>{{ modeNames[item.mode] }}</strong><template v-if="item.option?.available"><p>{{ item.option.duration_s == null ? '耗时待核实' : `${Math.ceil(item.option.duration_s / 60)} 分钟` }} · {{ distance(item.option.distance_m) }} · {{ cost(item.option) }}</p><small>{{ item.option.cost_estimate?.assumptions || item.option.note }}；车辆、车位及自行车可用性须自行确认。</small></template><small v-else>{{ item.option?.note || (canQuery ? '尚未查询；可先查询并预览，不保证有可用路线' : '两端地点尚需核实，暂不能查询') }}</small></div>
          <button type="button" class="text-button" :disabled="!selectable(item.mode, item.option)" @click="emit('select', item.mode)">{{ actionLabel(item.mode, item.option) }}</button>
        </div>
      </details>
    </template>
  </div>
</template>

<style scoped>
.common-mode-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr)); gap:10px; margin:10px 0; }
.common-mode-card { display:flex; flex-direction:column; gap:8px; padding:12px; border:1px solid #dbe7e1; border-radius:12px; background:#fff; min-width:0; overflow-wrap:anywhere; }
.common-mode-card.selected { border-color:#4b8372; background:#f2f8f4; }
.common-mode-card strong,.common-mode-card span { font-size:13px; }
.common-mode-card small,.additional-mode small { display:block; color:#728277; font-size:12px; line-height:1.6; }
.common-mode-card button { margin-top:auto; align-self:flex-start; }
.mode-badge { color:#287768; background:#e1f1e7; border-radius:5px; padding:2px 5px; white-space:nowrap; }
.recommendation-note { font-size:12px; color:#34735f; line-height:1.7; }
.additional-modes { margin-top:10px; }
.additional-mode { display:flex; justify-content:space-between; gap:10px; padding:12px 0; border-bottom:1px solid #e4ebe5; }
.additional-mode>div { min-width:0; overflow-wrap:anywhere; }
.additional-mode button { flex-shrink:0; align-self:flex-start; }
summary { cursor:pointer; font-size:12px; color:#4b6c5c; }
</style>
