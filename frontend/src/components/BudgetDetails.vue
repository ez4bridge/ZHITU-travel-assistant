<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Wallet, X } from 'lucide-vue-next'
import type { Plan } from '../types'
const props = defineProps<{ open: boolean; plan: Plan }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const money = (cents: number) => (cents / 100).toFixed(2)
const groups = computed(() => {
  const rows = props.plan.cost_breakdown || []
  return [...props.plan.itinerary.days.map((day, index) => ({ title: `第 ${index + 1} 天 · ${day.date}`, rows: rows.filter(row => row.day_index === index) })),
    { title: '往返及跨城交通', rows: rows.filter(row => row.day_index === null) }].filter(group => group.rows.length)
})
watch(() => props.open, async open => {
  await nextTick()
  if (open && !dialog.value?.open) dialog.value?.showModal()
  else if (!open && dialog.value?.open) dialog.value.close()
})
</script>

<template>
  <dialog ref="dialog" class="history-modal budget-modal card" aria-labelledby="budget-details-title" @close="emit('close')" @click="($event.target === dialog) && dialog?.close()">
    <div class="mini-heading"><Wallet :size="20" /><h3 id="budget-details-title">预算明细</h3><button class="icon-button" aria-label="关闭预算明细" autofocus @click="dialog?.close()"><X :size="20" /></button></div>
    <p class="budget-summary">全员 {{ plan.request.people }} 人 · 估算 ¥{{ money(plan.validation.total_cents) }} / 预算上限 ¥{{ money(plan.validation.budget_cents) }}</p>
    <p v-if="plan.validation.total_cents > plan.validation.budget_cents" class="subtle-note">超出预算 ¥{{ money(plan.validation.total_cents - plan.validation.budget_cents) }}，当前方案尚未达标。</p>
    <section v-for="group in groups" :key="group.title" class="budget-detail-group">
      <strong>{{ group.title }}</strong><span class="budget-group-total">小计 ¥{{ money(group.rows.reduce((sum, row) => sum + row.subtotal_cents, 0)) }}</span>
      <article v-for="(row, i) in group.rows" :key="i"><p>{{ row.label }}</p><p>¥{{ row.unit_cost }} × {{ row.quantity }} 次<span v-if="row.basis === 'person'"> × {{ row.people_multiplier }} 人</span><span v-else>（全员）</span> = <strong>¥{{ money(row.subtotal_cents) }}</strong></p><small>{{ row.note }}</small></article>
    </section>
    <p v-if="!groups.length" class="subtle-note">旧记录请先点击“核对并同步费用”，以补全逐项账本。</p>
    <p class="budget-summary">明细合计 ¥{{ money((plan.cost_breakdown || []).reduce((sum, row) => sum + row.subtotal_cents, 0)) }}</p>
    <p class="subtle-note">单价先按分取整，再乘次数和人数；所有金额来自同一账本。地图人均消费不是菜单报价，估算门票不是官方核实票价。</p>
  </dialog>
</template>
