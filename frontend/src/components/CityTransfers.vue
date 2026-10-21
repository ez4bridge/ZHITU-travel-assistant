<script setup lang="ts">
import { ArrowRight, Route } from 'lucide-vue-next'
import { computed } from 'vue'
import type { CityTransfer, IntercityMode, TripRequest } from '../types'
import { transferCoverage, transportScopeText } from '../transportScope'

const props = defineProps<{ transfers: CityTransfer[]; request: TripRequest }>()
const coverage = computed(() => transferCoverage(props.request, props.transfers))
const names: Record<IntercityMode, string> = { train: '铁路', flight: '飞机', bus: '长途汽车', driving: '自驾' }
</script>

<template>
  <section v-if="transfers.length || request.origin || coverage.expected.length" class="intercity-card card" aria-label="跨城交通安排">
    <div class="mini-heading"><Route :size="18" /><h3>把城市串成旅程</h3><span>跨城交通安排</span></div>
    <p class="intercity-intro">本版本交通范围：{{ transportScopeText(request) }}</p>
    <p v-if="!coverage.complete" class="transport-incomplete" role="alert">{{ coverage.missing.length ? '缺少交通段：' + coverage.missing.map(t => `${t.from} → ${t.to}`).join('、') : '交通段顺序与需求不一致' }}。当前预算尚不完整，请重新规划。</p>
    <p v-if="transfers.length" class="intercity-intro">以下已列交通费用已计入全员总预算。车票尚未预订；余票为查询时快照，参考票价不代表成交价，进出站接驳请另行确认。</p>
    <p v-else-if="coverage.complete" class="intercity-intro">本版本未包含出发或返程车票／机票，预算不含这部分费用。需要加入时，请勾选左侧选项后点击“重新规划一份行程”。</p>
    <p v-if="transfers.some(t => t.mode === 'flight')" class="intercity-intro">飞机交通目前提供时间与费用估算，未接入实时航班和机票查询。</p>
    <article v-for="(transfer, index) in transfers" :key="index" class="intercity-leg">
      <div class="intercity-heading"><strong>{{ transfer.from_city }} <ArrowRight :size="14" /> {{ transfer.to_city }}</strong><span>{{ names[transfer.mode] }} · {{ transfer.schedule_status === 'queried' ? '时刻已查询' : '估算' }}</span></div>
      <div class="intercity-times"><span>{{ transfer.departure_date }} {{ transfer.departure_time }}</span><ArrowRight :size="13" /><span>{{ transfer.arrival_date }} {{ transfer.arrival_time }}</span></div>
      <p v-if="transfer.schedule_status === 'queried'" class="intercity-stations">{{ transfer.train_code }} · {{ transfer.departure_station }} → {{ transfer.arrival_station }} · {{ transfer.seat_type }}（查询时余票：{{ transfer.seat_availability }}）</p>
      <p class="intercity-price">¥{{ transfer.unit_cost }} / {{ transfer.basis === 'person' ? '人' : '全员该段' }} · {{ transfer.price_status === 'reference' ? '参考票价' : '估算费用' }} <span>{{ transfer.note }}</span></p>
      <p v-if="transfer.queried_at" class="subtle-note">{{ transfer.source }} · 查询于 {{ new Date(transfer.queried_at).toLocaleString('zh-CN') }}</p>
      <p v-if="transfer.query_note" class="subtle-note">{{ transfer.query_note }}</p>
    </article>
  </section>
</template>

<style scoped>
.transport-incomplete { margin: 12px 0; color: #a45d32; font-size: 13px; line-height: 1.8; }
</style>
