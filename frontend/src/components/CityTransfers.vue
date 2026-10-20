<script setup lang="ts">
import { ArrowRight, Route } from 'lucide-vue-next'
import type { CityTransfer, IntercityMode } from '../types'

defineProps<{ transfers: CityTransfer[] }>()
const names: Record<IntercityMode, string> = { train: '铁路', flight: '飞机', bus: '长途汽车', driving: '自驾' }
</script>

<template>
  <section v-if="transfers.length" class="intercity-card card" aria-label="跨城交通安排">
    <div class="mini-heading"><Route :size="18" /><h3>把城市串成旅程</h3><span>跨城交通安排</span></div>
    <p class="intercity-intro">费用已计入全员总预算。车票尚未预订；余票为查询时快照，参考票价不代表成交价，进出站接驳请另行确认。</p>
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
