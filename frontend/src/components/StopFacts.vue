<script setup lang="ts">
import type { Stop, Expense } from '../types'
import { expenseFormula } from '../expenseFormula'
defineProps<{ stop: Stop; expenses: Expense[]; people: number }>()
const names: Record<string, string> = { ticket: '门票', food: '餐饮', hotel: '住宿', other: '其他' }
</script>

<template>
  <div class="stop-facts">
    <p v-if="stop.kind === 'meal' && stop.food_recommendations?.length">推荐尝试：{{ stop.food_recommendations.join('、') }}（菜品供应尚未核实）</p>
    <p v-if="stop.kind === 'meal' && stop.food_reference">高德特色信息：{{ stop.food_reference }}（地图参考）</p>
    <p v-if="stop.anchor_note">{{ stop.anchor_note }}<span v-if="stop.location_status === 'verified'"> · 通勤点：{{ stop.matched_name }}</span></p>
    <p v-for="(cost, i) in expenses" :key="i">{{ names[cost.category] || cost.category }}估算：{{ expenseFormula(cost, people) }}<span v-if="cost.note.includes('分摊')">（日预算分摊）</span></p>
    <p v-if="!expenses.length && (stop.kind === 'sight' || stop.kind === 'meal')">{{ stop.kind === 'sight' ? '门票' : '餐饮' }}：暂无对应费用参考，不视为免费</p>
    <p v-if="stop.opening_hours">{{ stop.opening_hours_scope === 'area' ? '区域营业参考' : '营业参考' }}：{{ stop.opening_hours }}</p>
    <p v-else-if="stop.opening_hours_today">查询当天营业参考：{{ stop.opening_hours_today }}（不代表出行日）</p>
    <p v-else-if="stop.kind !== 'break'">营业时间：暂未查到</p>
    <details v-if="expenses.length || stop.information_source">
      <summary>查看费用与营业信息依据</summary>
      <p v-for="(cost, i) in expenses" :key="i">{{ cost.label }}：{{ cost.note }}</p>
      <p v-if="stop.average_cost_yuan != null">高德人均消费参考 ¥{{ stop.average_cost_yuan }}，不是门票或本次菜单报价。</p>
      <p v-if="stop.information_source"><a :href="stop.information_source" target="_blank" rel="noopener noreferrer">高德地点信息来源</a><span v-if="stop.information_queried_at"> · 查询于 {{ new Date(stop.information_queried_at).toLocaleString('zh-CN') }}</span></p>
      <p>费用均为估算；地图营业信息可能滞后，出行日开放、预约和临时闭园请以官方为准。</p>
    </details>
  </div>
</template>
