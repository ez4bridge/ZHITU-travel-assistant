<script setup lang="ts">
import type { Plan } from '../types'
import { expenseFormula } from '../expenseFormula'

defineProps<{ itinerary: Plan['itinerary']; people: number }>()
</script>

<template>
  <details class="budget-details">
    <summary>查看费用明细与计算方式</summary>
    <p class="budget-help">按人计费会另乘同行人数；数量表示消费份数。以下沿用行程中的估算单价。</p>
    <section v-for="(day, index) in itinerary.days" :key="day.date">
      <h4>第{{ index + 1 }}天 · {{ day.date }}</h4>
      <p v-for="(cost, i) in day.expenses" :key="i"><span>{{ cost.label }}</span><strong>{{ expenseFormula(cost, people) }}</strong></p>
    </section>
    <section v-if="itinerary.transfers?.length">
      <h4>跨城交通</h4>
      <p v-for="(transfer, index) in itinerary.transfers" :key="index"><span>{{ transfer.from_city }} → {{ transfer.to_city }}</span><strong>{{ expenseFormula({ unit_cost: transfer.unit_cost, quantity: 1, basis: transfer.basis }, people) }}</strong></p>
    </section>
  </details>
</template>

<style scoped>
.budget-details { margin-top: 18px; border-top: 1px solid #e5ebe2; padding-top: 12px; font-size: 12px; color: #627b66; }
summary { cursor: pointer; color: #426b52; }
.budget-help { line-height: 1.7; color: #7a8d7c; }
section { padding-top: 6px; }
h4 { margin: 8px 0; font-size: 12px; color: #426b52; }
section p { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; margin: 9px 0; line-height: 1.7; overflow-wrap: anywhere; }
strong { font-weight: 500; }
</style>
