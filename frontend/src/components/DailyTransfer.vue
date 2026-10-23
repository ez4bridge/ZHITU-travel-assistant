<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, BusFront, CarFront, Plane, TrainFront } from 'lucide-vue-next'
import IntercityLinks from './IntercityLinks.vue'
import type { CityTransfer, IntercityMode } from '../types'

const props = withDefaults(defineProps<{
  transfer: CityTransfer; index: number; people?: number; continuedFromPrevious?: boolean; continuesNextDay?: boolean
}>(), { people: 1, continuedFromPrevious: false, continuesNextDay: false })
const names: Record<IntercityMode, string> = { train: '铁路', flight: '飞机', bus: '长途汽车', driving: '自驾' }
const icons = { train: TrainFront, flight: Plane, bus: BusFront, driving: CarFront }
const status = computed(() => props.transfer.booking_status === 'user_confirmed' ? '用户已填预订，未查验订单'
  : props.transfer.schedule_status === 'queried' ? '时刻已查询，未预订' : '规划估算，待确认班次')
const groupCost = computed(() => {
  const amount = Number(props.transfer.unit_cost)
  return Number.isFinite(amount) ? (amount * (props.transfer.basis === 'person' ? props.people : 1)).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '待核实'
})
const serviceLabel = computed(() => {
  if (props.transfer.train_code) return props.transfer.train_code
  if (props.transfer.mode === 'flight') return '航班号待选择'
  if (props.transfer.mode === 'train') return '车次待确认'
  return ''
})
</script>

<template>
  <article class="daily-transfer" :aria-label="`${transfer.from_city}至${transfer.to_city}跨城交通`">
    <div class="transfer-badge"><component :is="icons[transfer.mode]" :size="17" /><span>{{ names[transfer.mode] }} · 第 {{ index + 1 }} 段跨城交通</span><small v-if="continuedFromPrevious">承接前一天</small><small v-else-if="continuesNextDay">跨夜到达</small></div>
    <h4>{{ transfer.from_city }} <ArrowRight :size="15" /> {{ transfer.to_city }}</h4>
    <p class="transfer-status">{{ status }}</p>
    <div class="transfer-times"><span><small>出发</small><strong>{{ transfer.departure_time }}</strong><time>{{ transfer.departure_date }}</time><b>{{ transfer.departure_station || '站点 / 机场待确认' }}</b></span><ArrowRight :size="17" /><span><small>到达</small><strong>{{ transfer.arrival_time }}</strong><time>{{ transfer.arrival_date }}</time><b>{{ transfer.arrival_station || '站点 / 机场待确认' }}</b></span></div>
    <p v-if="continuedFromPrevious" class="transfer-note">此段交通从前一天延续至本日，到达后还需预留出站与市内接驳时间。</p>
    <p v-if="serviceLabel" class="transfer-service">{{ serviceLabel }}<span v-if="transfer.seat_type"> · {{ transfer.seat_type }}</span><span v-if="transfer.schedule_status === 'queried' && transfer.seat_availability"> · 查询时余票：{{ transfer.seat_availability }}</span></p>
    <p class="transfer-price">全员该段约 ¥{{ groupCost }} <small>{{ transfer.price_status === 'user_reported' ? '用户填写的总额' : transfer.price_status === 'reference' ? '参考票价' : '估算费用' }} · 已计入整趟预算{{ continuedFromPrevious ? '，跨夜延续展示不重复计费' : '' }}</small></p>
    <p v-if="transfer.basis === 'person'" class="transfer-note">每人 ¥{{ transfer.unit_cost }} × {{ people }} 人；实际成交金额以订单为准。</p>
    <p v-if="transfer.note" class="transfer-note">{{ transfer.note }}</p>
    <details v-if="transfer.source || transfer.queried_at || transfer.query_note" class="transfer-source"><summary>来源与查询说明</summary><p v-if="transfer.source">{{ transfer.source }}</p><p v-if="transfer.queried_at">查询于 {{ new Date(transfer.queried_at).toLocaleString('zh-CN') }}，查询结果不代表占座或锁价。</p><p v-if="transfer.query_note">{{ transfer.query_note }}</p></details>
    <IntercityLinks v-if="transfer.mode === 'flight' && transfer.booking_status !== 'user_confirmed'" :from-city="transfer.from_city" :to-city="transfer.to_city" :travel-date="transfer.departure_date" />
    <a v-if="transfer.mode === 'train' && transfer.booking_status !== 'user_confirmed'" class="rail-query" href="https://www.12306.cn/index/" target="_blank" rel="noopener noreferrer">去 12306 核对车次与购票 <ArrowRight :size="13" /></a>
  </article>
</template>

<style scoped>
.daily-transfer { margin: 10px 0 20px; padding: 17px; background: #f0f6ef; border: 1px solid #cfdfca; border-left: 3px solid #6e9970; border-radius: 12px; min-width: 0; overflow-wrap: anywhere; }
.transfer-badge { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; color: #608765; font-size: 11px; }
.transfer-badge small { padding: 3px 7px; background: #e2ecd9; border-radius: 6px; font-size: 10px; }
.daily-transfer h4 { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 15px; color: #315640; margin: 11px 0 6px; }
.transfer-status { font-size: 10px; color: #7f906f; line-height: 1.7; }
.transfer-times { display: grid; grid-template-columns: minmax(0, 1fr) 20px minmax(0, 1fr); align-items: start; gap: 12px; margin: 15px 0 12px; }
.transfer-times > span { display: grid; gap: 5px; min-width: 0; }
.transfer-times > svg { color: #87a277; margin-top: 26px; }
.transfer-times small, .transfer-times time { font-size: 10px; color: #8a977c; }
.transfer-times strong { font-size: 21px; color: #3c6449; font-weight: 500; }
.transfer-times b { font-size: 11px; color: #66815b; font-weight: 500; }
.transfer-service { font-size: 12px; line-height: 1.8; color: #57784f; }
.transfer-price { font-size: 14px; color: #3a704d; margin-top: 11px; line-height: 1.8; }
.transfer-price small { display: block; color: #7f936f; font-size: 10px; }
.transfer-note, .transfer-source { font-size: 10px; color: #839275; line-height: 1.9; margin-top: 7px; }
.transfer-source summary { cursor: pointer; color: #6b895e; }
.transfer-source p { margin-top: 6px; }
.rail-query { display: inline-flex; align-items: center; gap: 7px; color: #37775b; font-size: 11px; text-decoration: underline; margin-top: 12px; line-height: 1.8; }
@media (max-width: 520px) { .daily-transfer { padding: 13px; } .transfer-times { gap: 8px; } .transfer-times strong { font-size: 20px; } }
</style>
