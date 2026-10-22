<script setup lang="ts">
import { BedDouble, ChevronRight, Coffee, Landmark, Leaf, MapPin } from 'lucide-vue-next'
import StudyStopDetails from './StudyStopDetails.vue'
import StopFacts from './StopFacts.vue'
import type { Expense, Stop, StudyContext } from '../types'

defineProps<{ stop: Stop; index: number; selected: boolean; context?: StudyContext | null; expenses?: Expense[]; people?: number }>()
const emit = defineEmits<{ select: [id: string] }>()
const icons = { sight: Landmark, meal: Coffee, hotel: BedDouble, break: Leaf }
const kinds = { sight: '景点', meal: '餐饮', hotel: '住宿', break: '自由活动' }
const meals = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐' }
</script>

<template>
  <article :id="stop.id" class="itinerary-stop" :class="{ active: selected }" :aria-labelledby="stop.id + '-name'">
    <button type="button" class="stop-select" :aria-pressed="selected" :aria-label="'在地图上查看' + stop.name" @click="emit('select', stop.id)">
      <span class="stop-heading-row"><span class="stop-number">{{ String(index + 1).padStart(2, '0') }}</span><span class="stop-time">{{ stop.start }}—{{ stop.end }}</span><span class="stop-kind"><component :is="icons[stop.kind]" :size="13" />{{ stop.kind === 'meal' && stop.meal_period ? meals[stop.meal_period] : kinds[stop.kind] }}</span><ChevronRight :size="15" class="map-select-arrow" /></span>
      <h4 :id="stop.id + '-name'"><small v-if="stop.city && !stop.study" class="stop-city">{{ stop.city }} · </small>{{ stop.name }}</h4>
      <span v-if="stop.address" class="stop-location"><MapPin :size="14" /><span>{{ stop.address }}</span></span>
      <span v-else-if="stop.location_status === 'verified' && stop.matched_name && stop.matched_name !== stop.name" class="stop-location"><MapPin :size="14" /><span>{{ stop.matched_name }}</span></span>
      <span v-if="stop.location_status !== 'verified' && stop.kind !== 'break'" class="stop-unverified">地点待核对</span>
    </button>
    <div class="stop-content">
      <section v-if="stop.study?.guide || stop.description" class="stop-story"><h5>{{ stop.study ? '历史讲解' : '行程安排' }}</h5><p>{{ stop.study?.guide || stop.description }}</p></section>
      <StudyStopDetails v-if="stop.study && context" :stop="stop" :context="context" />
      <StopFacts v-if="!stop.study" :stop="stop" :expenses="expenses || []" :people="people || 1" />
    </div>
  </article>
</template>

<style scoped>
.itinerary-stop { min-width: 0; scroll-margin-top: 18px; background: #fff; border: 1px solid #e2e9e0; border-radius: 14px; transition: border-color .15s, box-shadow .15s; }
.itinerary-stop + .itinerary-stop { margin-top: 14px; }
.itinerary-stop.active { border-color: #87b6a4; box-shadow: 0 0 0 1px #87b6a41a; }
.itinerary-stop:focus-within { border-color: #75a995; }
.itinerary-stop:has(.stop-select:focus-visible) { outline: 2px solid #5c9f8b; outline-offset: 3px; }
.stop-select { display: block; width: 100%; padding: 18px 18px 0; color: inherit; text-align: left; border-radius: 14px 14px 0 0; }
.stop-select:hover h4 { color: #147b71; }
.stop-select:focus-visible { outline: none; }
.stop-heading-row { display: flex; align-items: center; gap: 9px; min-width: 0; }
.stop-number { display: grid; place-items: center; flex-shrink: 0; width: 27px; height: 27px; border-radius: 50%; background: #eef4ef; color: #527e6c; font-size: 11px; }
.stop-time { color: #61786a; font-size: 12px; letter-spacing: .15px; }
.stop-kind { margin-left: auto; display: inline-flex; align-items: center; gap: 4px; color: #7a8d7f; font-size: 11px; white-space: nowrap; }
.map-select-arrow { color: #7c9c8c; flex-shrink: 0; }
.stop-select h4 { margin: 12px 0 8px; color: #274e42; font-size: 16px; font-weight: 600; line-height: 1.65; overflow-wrap: anywhere; }
.stop-location { display: flex; align-items: flex-start; gap: 6px; color: #788c7f; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.stop-location svg { margin-top: 3px; flex-shrink: 0; }
.stop-unverified { display: block; margin-top: 5px; font-size: 11px; color: #a17c49; }
.stop-content { padding: 18px; }
.stop-story h5 { margin: 0 0 7px; color: #426754; font-size: 12px; font-weight: 600; }
.stop-story p { margin: 0; color: #5d7165; font-size: 14px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
@media(max-width:520px) { .stop-select { padding: 16px 16px 0; } .stop-content { padding: 16px; } .stop-heading-row { gap: 7px; } .stop-time { font-size: 11px; } }
</style>
