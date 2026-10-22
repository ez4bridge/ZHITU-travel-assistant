<script setup lang="ts">
import { computed, useId } from 'vue'
import { ArrowRight, Plus, Ticket, Trash2 } from 'lucide-vue-next'
import type { BookedTransport } from '../bookingTypes'
import type { IntercityMode } from '../types'

const props = withDefaults(defineProps<{
  modelValue: BookedTransport[]; cities: string[]; startDate: string; disabled?: boolean
}>(), { disabled: false })
const emit = defineEmits<{ 'update:modelValue': [items: BookedTransport[]] }>()
const listId = `booking-cities-${useId()}`
const MODES: { value: IntercityMode; label: string }[] = [
  { value: 'train', label: '铁路' }, { value: 'flight', label: '飞机' },
  { value: 'bus', label: '长途汽车' }, { value: 'driving', label: '自驾' },
]
const cityChoices = computed(() => [...new Set(props.cities.map(city => city.trim()).filter(Boolean))])

function update(index: number, patch: Partial<BookedTransport>) {
  emit('update:modelValue', props.modelValue.map((item, i) => i === index ? { ...item, ...patch } : item))
}
function add() {
  if (props.disabled || props.modelValue.length >= 14) return
  emit('update:modelValue', [...props.modelValue, {
    transfer_index: null, from_city: '', to_city: '', mode: 'train',
    departure_date: props.startDate, departure_time: '',
    arrival_date: props.startDate, arrival_time: '',
    number: '', departure_place: '', arrival_place: '', total_cost: null,
    departure_buffer_min: null, arrival_buffer_min: null,
  }])
}
function remove(index: number) {
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}
function optionalNumber(event: Event) {
  const value = (event.target as HTMLInputElement).value
  return value.trim() ? Number(value) : null
}
function revealInvalid(event: Event) {
  let parent = (event.target as HTMLElement).parentElement
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
    parent = parent.parentElement
  }
}
</script>

<template>
  <div class="transport-fields">
    <p v-if="!modelValue.length" class="empty">还没有填写已订交通。未订好可以留空，生成后再补录。</p>
    <article v-for="(item, index) in modelValue" :key="index" class="row">
      <header>
        <strong><Ticket :size="14" /> 已订交通 {{ index + 1 }}</strong>
        <button type="button" :disabled="disabled" :aria-label="`移除已订交通 ${index + 1}`" @click="remove(index)"><Trash2 :size="14" /> 移除</button>
      </header>
      <div class="grid">
        <label><span>方式</span><select :value="item.mode" :disabled="disabled" :aria-label="`已订交通 ${index + 1} 方式`" @change="update(index, { mode: ($event.target as HTMLSelectElement).value as IntercityMode })"><option v-for="mode in MODES" :key="mode.value" :value="mode.value">{{ mode.label }}</option></select></label>
        <label><span>车次 / 航班号</span><input :value="item.number || ''" :disabled="disabled" maxlength="30" placeholder="可选" :aria-label="`已订交通 ${index + 1} 车次`" @input="update(index, { number: ($event.target as HTMLInputElement).value })" /></label>
        <label><span>出发城市</span><input :value="item.from_city" :disabled="disabled" list="listId" maxlength="40" required :aria-label="`已订交通 ${index + 1} 出发城市`" @input="update(index, { from_city: ($event.target as HTMLInputElement).value })" @invalid="revealInvalid" /></label>
        <label><span>到达城市</span><input :value="item.to_city" :disabled="disabled" maxlength="40" required :aria-label="`已订交通 ${index + 1} 到达城市`" @input="update(index, { to_city: ($event.target as HTMLInputElement).value })" @invalid="revealInvalid" /></label>
        <label><span>出发日期</span><input type="date" :value="item.departure_date" :disabled="disabled" required :aria-label="`已订交通 ${index + 1} 出发日期`" @input="update(index, { departure_date: ($event.target as HTMLInputElement).value })" @invalid="revealInvalid" /></label>
        <label><span>出发时间</span><input type="time" :value="item.departure_time" :disabled="disabled" required :aria-label="`已订交通 ${index + 1} 出发时间`" @input="update(index, { departure_time: ($event.target as HTMLInputElement).value })" @invalid="revealInvalid" /></label>
        <label><span>到达日期</span><input type="date" :value="item.arrival_date" :min="item.departure_date" :disabled="disabled" required :aria-label="`已订交通 ${index + 1} 到达日期`" @input="update(index, { arrival_date: ($event.target as HTMLInputElement).value })" @invalid="revealInvalid" /></label>
        <label><span>到达时间</span><input type="time" :value="item.arrival_time" :disabled="disabled" required :aria-label="`已订交通 ${index + 1} 到达时间`" @input="update(index, { arrival_time: ($event.target as HTMLInputElement).value })" @invalid="revealInvalid" /></label>
        <label><span>出发车站 / 机场</span><input :value="item.departure_place || ''" :disabled="disabled" maxlength="120" placeholder="可选，填全称更准" :aria-label="`已订交通 ${index + 1} 出发站点`" @input="update(index, { departure_place: ($event.target as HTMLInputElement).value })" /></label>
        <label><span>到达车站 / 机场</span><input :value="item.arrival_place || ''" :disabled="disabled" maxlength="120" placeholder="可选，填全称更准" :aria-label="`已订交通 ${index + 1} 到达站点`" @input="update(index, { arrival_place: ($event.target as HTMLInputElement).value })" /></label>
        <label><span>全员总费用（元）</span><input type="number" min="0" step="0.01" :value="item.total_cost ?? ''" :disabled="disabled" placeholder="可选" :aria-label="`已订交通 ${index + 1} 全员总费用`" @input="update(index, { total_cost: optionalNumber($event) })" /></label>
      </div>
    </article>
    <datalist :id="listId"><option v-for="city in cityChoices" :key="city" :value="city" /></datalist>
    <button type="button" class="add" :disabled="disabled || modelValue.length >= 14" @click="add"><Plus :size="14" /> 添加一段已订交通</button>
    <p class="hint"><ArrowRight :size="13" /> 只填已经买好的车票／机票。系统按你填的时刻与站点排程，不查验订单，也不会代订。</p>
  </div>
</template>

<style scoped>
.transport-fields { display: grid; gap: 12px; }
.empty, .hint { margin: 0; font-size: 11px; color: #8a957e; line-height: 1.8; }
.hint { display: flex; align-items: center; gap: 6px; }
.row { border: 1px solid #e2ead9; border-radius: 10px; padding: 12px; background: #fbfcf8; }
.row header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; }
.row header strong { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: #4c6b45; }
.row header button { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: #9a6b5e; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.grid label { display: grid; gap: 4px; font-size: 11px; color: #7b8a70; }
.grid input, .grid select { width: 100%; padding: 7px 9px; border: 1px solid #dde6d5; border-radius: 8px; font-size: 12px; background: #fff; }
.add { justify-self: start; display: inline-flex; align-items: center; gap: 6px; padding: 8px 11px; border: 1px dashed #c9d9bd; border-radius: 9px; font-size: 12px; color: #577e46; background: #f7faf1; }
@media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }
</style>
