<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { ArrowUp, ArrowDown, Plus, X, MapPin } from 'lucide-vue-next'
import { api } from '../api'
import type { DestinationStay, CitySuggestion } from '../types'

const props = defineProps<{ modelValue: DestinationStay[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: DestinationStay[]] }>()
const suggestions = ref<CitySuggestion[]>([]), activeIndex = ref(-1), suggestionNote = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let requestId = 0
const durations = Array.from({ length: 20 }, (_, index) => (index + 1) / 2)

function patch(index: number, changes: Partial<DestinationStay>) {
  if (props.disabled) return
  emit('update:modelValue', props.modelValue.map((row, i) => i === index ? { ...row, ...changes } : row))
}
function closeSuggestions() {
  clearTimeout(timer); requestId++; activeIndex.value = -1; suggestions.value = []; suggestionNote.value = ''
}
function search(index: number, value: string) {
  clearTimeout(timer)
  const id = ++requestId
  activeIndex.value = index; suggestions.value = []; suggestionNote.value = ''
  const query = value.trim()
  if (!query) return
  timer = setTimeout(async () => {
    try {
      const items = await api<CitySuggestion[]>(`/api/cities?query=${encodeURIComponent(query)}`)
      if (id !== requestId) return
      suggestions.value = items
      suggestionNote.value = items.length ? '' : '没有匹配建议，可直接输入目的地城市。'
    } catch {
      if (id === requestId) suggestionNote.value = '城市建议暂不可用，可直接输入城市。'
    }
  }, 350)
}
function changeCity(index: number, event: Event) {
  const value = (event.target as HTMLInputElement).value
  patch(index, { city: value }); search(index, value)
}
function changeDuration(index: number, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  patch(index, { preferred_days: Number(value) })
}
function selectCity(index: number, city: CitySuggestion) {
  patch(index, { city: city.name }); closeSuggestions()
}
function add() {
  if (props.disabled || props.modelValue.length >= 5) return
  closeSuggestions()
  emit('update:modelValue', [...props.modelValue, { city: '', preferred_days: 1 }])
}
function remove(index: number) {
  if (props.disabled || props.modelValue.length <= 1) return
  closeSuggestions()
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}
function move(index: number, offset: number) {
  const target = index + offset
  if (props.disabled || target < 0 || target >= props.modelValue.length) return
  closeSuggestions()
  const rows = [...props.modelValue]
  ;[rows[index], rows[target]] = [rows[target]!, rows[index]!]
  emit('update:modelValue', rows)
}
function onFocusOut(event: FocusEvent) {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) closeSuggestions()
}
onBeforeUnmount(closeSuggestions)
</script>

<template>
  <div class="destination-editor" @focusout="onFocusOut">
    <div class="destination-heading"><span>目的地城市 <b>*</b></span><small>{{ modelValue.length }} / 5</small></div>
    <p class="destination-help">按游玩顺序添加，每个城市分别选择游玩天数。</p>
    <div v-for="(row, index) in modelValue" :key="index" class="destination-row">
      <div class="destination-row-title"><span>第 {{ index + 1 }} 站</span><div class="destination-actions">
        <button type="button" :disabled="disabled || index === 0" :aria-label="`上移第${index + 1}站`" @click="move(index, -1)"><ArrowUp :size="14" /></button>
        <button type="button" :disabled="disabled || index === modelValue.length - 1" :aria-label="`下移第${index + 1}站`" @click="move(index, 1)"><ArrowDown :size="14" /></button>
        <button type="button" :disabled="disabled || modelValue.length === 1" :aria-label="`删除第${index + 1}站`" @click="remove(index)"><X :size="14" /></button>
      </div></div>
      <div class="destination-inputs">
        <div class="city-input-cell">
          <div class="input-wrap"><MapPin :size="16" /><input :value="row.city" required maxlength="40" autocomplete="off" :disabled="disabled" :aria-label="`第${index + 1}站城市`" :aria-expanded="activeIndex === index && suggestions.length > 0" :aria-controls="`city-suggestions-${index}`" placeholder="输入或搜索城市" @input="changeCity(index, $event)" @focus="search(index, row.city)" @keydown.esc="closeSuggestions" /></div>
          <ul v-if="activeIndex === index && suggestions.length" :id="`city-suggestions-${index}`" class="city-suggestions" :aria-label="`第${index + 1}站城市建议`">
            <li v-for="city in suggestions" :key="city.adcode"><button type="button" :disabled="disabled" @click="selectCity(index, city)"><span>{{ city.name }}</span><small>{{ city.province }}</small></button></li>
          </ul>
        </div>
        <label class="stay-select"><span>游玩天数</span><select :value="row.preferred_days ?? 1" :disabled="disabled" :aria-label="`第${index + 1}站游玩天数`" @focus="closeSuggestions" @change="changeDuration(index, $event)"><option v-for="duration in durations" :key="duration" :value="duration">{{ duration === 0.5 ? '半天' : `${duration} 天` }}</option></select></label>
      </div>
      <p v-if="activeIndex === index && suggestionNote" class="field-hint" role="status">{{ suggestionNote }}</p>
    </div>
    <button type="button" class="add-destination" :disabled="disabled || modelValue.length >= 5" @click="add"><Plus :size="15" />{{ modelValue.length >= 5 ? '已达五个城市上限' : '添加目的地' }}</button>
    <p v-if="modelValue.length > 5" class="destination-limit" role="alert">旧行程包含超过五个城市；可查看，重新规划前请删减至五个。</p>
    <p class="destination-help">行程日期按各站天数合计安排，合计不足整天时向上取整。交通会占用部分游玩时间，未满足时会提示。</p>
  </div>
</template>
