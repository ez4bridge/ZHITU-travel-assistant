<script setup lang="ts">
import { computed } from 'vue'
import { BookOpen, Sparkles, ArrowRight, LoaderCircle, Footprints, CarFront } from 'lucide-vue-next'
import type { StudyCatalog, StudyOptions, TripRequest } from '../types'
import { useDepartureClock } from '../departure'

const { today, earliest } = useDepartureClock()

const props = defineProps<{ modelValue: TripRequest; catalog: StudyCatalog | null; busy: boolean; canPlan: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: TripRequest]; generate: [] }>()
const options = computed(() => props.modelValue.study!)
const value = (event: Event) => (event.target as HTMLInputElement).value
function update(patch: Partial<TripRequest>) { emit('update:modelValue', { ...props.modelValue, ...patch }) }
function updateStudy(patch: Partial<StudyOptions>) { update({ study: { ...options.value, ...patch } }) }
</script>

<template>
  <div class="panel-heading"><div class="icon-tile"><BookOpen :size="19" /></div><div><h3>带着问题，走进上海</h3><p>沿着资料中的线索寻找历史</p></div></div>
  <form class="study-form" @submit.prevent="emit('generate')">
    <fieldset :disabled="busy">
      <label class="field study-wide"><span>这次想了解什么？</span><textarea :value="options.topic" required rows="3" maxlength="500" placeholder="例如：《新青年》与当时的青年学习有什么联系？" @input="updateStudy({ topic: value($event) })"></textarea></label>
      <p class="study-scope study-wide">上海 · 单日研学<br />首批资料围绕近代青年、报刊编辑与出版活动。</p>
      <label class="field"><span>出行日期</span><div class="input-wrap"><input :value="modelValue.start_date" type="date" :min="today" required @input="update({ start_date: value($event) })" /></div></label>
      <div class="field-row"><label class="field"><span>首站开始</span><div class="input-wrap"><input :value="modelValue.start_time" type="time" :min="modelValue.start_date === today ? earliest.time : undefined" required @input="update({ start_time: value($event) })" /></div></label><label class="field"><span>最晚结束</span><div class="input-wrap"><input :value="options.end_time" type="time" required @input="updateStudy({ end_time: value($event) })" /></div></label></div>
      <div class="field-row"><label class="field"><span>同行人数</span><div class="input-wrap"><input :value="modelValue.people" type="number" min="1" max="50" required @input="update({ people: Number(value($event)) })" /><span class="input-unit">人</span></div></label><label class="field"><span>全员预算</span><div class="input-wrap"><input :value="modelValue.budget" type="number" min="1" step="0.01" max="10000000" required @input="update({ budget: Number(value($event)) })" /><span class="input-unit">元</span></div></label></div>
      <div class="field"><span>站间出行</span><div class="transport-options"><button type="button" :class="{ selected: modelValue.mode === 'walking' }" @click="update({ mode: 'walking' })"><Footprints :size="18" />步行探索</button><button type="button" :class="{ selected: modelValue.mode === 'driving' }" @click="update({ mode: 'driving' })"><CarFront :size="18" />驾车出行</button></div></div>
      <label v-if="modelValue.mode === 'walking'" class="field"><span>站间步行上限 <small>不含站内游览</small></span><div class="input-wrap"><input :value="options.max_walking_m / 1000" type="number" min="0.5" max="10" step="0.5" required @input="updateStudy({ max_walking_m: Math.round(Number(value($event)) * 1000) })" /><span class="input-unit">公里</span></div></label>
      <label class="field study-wide"><span>还有什么期待？</span><textarea :value="modelValue.preferences" rows="2" maxlength="2000" placeholder="例如：多讲陈望道，留些休息时间。" @input="update({ preferences: value($event) })"></textarea></label>
      <button class="primary-button generate-button study-wide" type="submit" :disabled="!canPlan || !catalog?.ready"><LoaderCircle v-if="busy" :size="18" class="spin" /><Sparkles v-else :size="18" />{{ busy ? '正在寻找历史线索' : '生成我的研学行程' }}<ArrowRight v-if="!busy" :size="17" /></button>
    </fieldset>
  </form>
  <p class="form-note">时间从首站开始；依据资料安排参观，临时开放、预约和费用仍需出发前核对。</p>
  <p v-if="catalog && !catalog.ready" class="config-notice">{{ catalog.message }}</p>
</template>
