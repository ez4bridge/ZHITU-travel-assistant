<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { BedDouble, Plus, Trash2 } from 'lucide-vue-next'
import type { ConfirmedHotel } from '../bookingTypes'

const props = withDefaults(defineProps<{
  modelValue: ConfirmedHotel[]; cities: string[]; startDate: string; days?: number; disabled?: boolean; compact?: boolean
}>(), { days: 1, disabled: false, compact: false })
const emit = defineEmits<{ 'update:modelValue': [hotels: ConfirmedHotel[]] }>()
const cityListId = `hotel-cities-${useId()}`
const invalidHotel = ref(false)
const cityChoices = computed(() => [...new Set(props.cities.map(city => city.trim()).filter(Boolean))])

function afterDays(date: string, days: number) {
  const stamp = Date.parse(`${date}T00:00:00Z`)
  return Number.isFinite(stamp) ? new Date(stamp + days * 86400000).toISOString().slice(0, 10) : ''
}
function addHotel() {
  if (props.disabled || props.modelValue.length >= 20) return
  const city = cityChoices.value.find(choice => !props.modelValue.some(hotel => hotel.city === choice)) || cityChoices.value[0] || ''
  emit('update:modelValue', [...props.modelValue, {
    city, name: '', address: '', check_in: props.startDate,
    check_out: afterDays(props.startDate, Math.max(1, props.days)), total_cost: null,
  }])
}
function updateHotel(index: number, patch: Partial<ConfirmedHotel>) {
  emit('update:modelValue', props.modelValue.map((hotel, i) => i === index ? { ...hotel, ...patch } : hotel))
}
function inputValue(event: Event) { return (event.target as HTMLInputElement).value }
function removeHotel(index: number) {
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}
function costInput(event: Event) {
  const value = inputValue(event)
  return value.trim() ? Number(value) : null
}
function revealInvalid(event: Event) {
  invalidHotel.value = true
  // Hotel fields can live inside the request panel's collapsed optional section.
  // Open its details before native validity handling tries to focus the input.
  let parent = (event.target as HTMLElement).parentElement
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
    parent = parent.parentElement
  }
}
</script>

<template>
  <div class="hotel-fields" :class="{ compact }" @invalid.capture="revealInvalid" @input="invalidHotel = false">
    <p class="hotel-help">按每家酒店的入住、退房日期填写，可跨城市或中途换酒店。未订好时留空，先看住宿片区建议。</p>
    <p v-if="invalidHotel" class="hotel-invalid" role="alert">请补全已添加酒店的信息，或移除尚未确定的酒店，再继续。</p>
    <datalist :id="cityListId"><option v-for="city in cityChoices" :key="city" :value="city" /></datalist>
    <article v-for="(hotel, index) in modelValue" :key="index" class="hotel-row">
      <header><strong><BedDouble :size="15" /> 酒店 {{ index + 1 }}</strong><button type="button" :disabled="disabled" :aria-label="`移除酒店 ${index + 1}`" @click="removeHotel(index)"><Trash2 :size="15" /> 移除</button></header>
      <div class="hotel-grid">
        <label><span>对应目的地城市</span><input :value="hotel.city" :list="cityListId" :aria-label="`酒店 ${index + 1} 所在城市`" maxlength="40" required :disabled="disabled" placeholder="选择行程中的城市" @input="updateHotel(index, { city: inputValue($event) })" /></label>
        <label><span>酒店名称</span><input :value="hotel.name" :aria-label="`酒店 ${index + 1} 名称`" maxlength="120" required :disabled="disabled" placeholder="填写准确酒店及分店名" @input="updateHotel(index, { name: inputValue($event) })" /></label>
        <label class="hotel-address"><span>地址或分店位置（可选）</span><input :value="hotel.address || ''" :aria-label="`酒店 ${index + 1} 地址`" maxlength="240" :disabled="disabled" placeholder="同名分店请补充地址，帮助核对位置" @input="updateHotel(index, { address: inputValue($event) })" /></label>
        <label><span>入住日期</span><input type="date" :value="hotel.check_in" :aria-label="`酒店 ${index + 1} 入住日期`" required :disabled="disabled" @input="updateHotel(index, { check_in: inputValue($event) })" /></label>
        <label><span>退房日期</span><input type="date" :value="hotel.check_out" :aria-label="`酒店 ${index + 1} 退房日期`" :min="afterDays(hotel.check_in, 1)" :max="afterDays(hotel.check_in, 31)" required :disabled="disabled" @input="updateHotel(index, { check_out: inputValue($event) })" /></label>
        <label class="hotel-cost"><span>这次入住全员总房费（元，可选）</span><input type="number" :value="hotel.total_cost ?? ''" :aria-label="`酒店 ${index + 1} 全员总房费`" min="0" max="10000000" step="0.01" :disabled="disabled" placeholder="不清楚请留空，0 表示确认免费" @input="updateHotel(index, { total_cost: costInput($event) })" /></label>
      </div>
      <p class="hotel-help">退房日不计当晚住宿；只有前一晚住在这里，才从此酒店规划当天出发。到达当天先遵循实际到站时间。</p>
    </article>
    <button type="button" class="add-hotel" :disabled="disabled || modelValue.length >= 20" @click="addHotel"><Plus :size="15" /> {{ modelValue.length ? '再添加一家酒店' : '添加已确认酒店' }}</button>
  </div>
</template>

<style scoped>
.hotel-fields { min-width: 0; }
.hotel-help { font-size: 11px; line-height: 1.8; color: #82907e; margin: 0 0 10px; }
.hotel-invalid { font-size: 11px; line-height: 1.8; color: #a36f3d; margin-bottom: 11px; }
.hotel-row { margin: 12px 0; padding: 14px; border: 1px solid #e0e8da; border-radius: 12px; background: #fcfdfb; min-width: 0; }
.hotel-row header { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 13px; }
.hotel-row header strong, .hotel-row header button { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; }
.hotel-row header strong { color: #3f6950; }
.hotel-row header button { color: #8a907f; padding: 5px; font-size: 11px; }
.hotel-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 11px; }
.hotel-grid label { display: grid; gap: 7px; min-width: 0; font-size: 11px; color: #53705e; }
.hotel-grid input { width: 100%; min-width: 0; max-width: 100%; min-height: 42px; border: 1px solid #dce6d6; border-radius: 8px; padding: 10px; font-size: 12px; color: #345644; background: white; }
.hotel-grid input:focus { outline: 2px solid #aad1bb; outline-offset: 1px; }
.hotel-grid input::placeholder { font-size: 11px; color: #98a08e; }
.hotel-address, .hotel-cost { grid-column: 1 / -1; }
.hotel-row > .hotel-help { margin: 11px 0 0; font-size: 10px; }
.add-hotel { display: flex; width: 100%; justify-content: center; align-items: center; gap: 7px; padding: 11px; border: 1px dashed #bcd0b4; border-radius: 9px; color: #527b4a; font-size: 12px; }
.compact .hotel-grid { grid-template-columns: 1fr; }
.compact .hotel-row { padding: 11px; }
@media (max-width: 560px) { .hotel-grid { grid-template-columns: 1fr; } .hotel-row { padding: 11px; } }
</style>
