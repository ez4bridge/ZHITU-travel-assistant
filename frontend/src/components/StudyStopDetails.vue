<script setup lang="ts">
import { computed } from 'vue'
import { PencilLine } from 'lucide-vue-next'
import SourceDrawer from './SourceDrawer.vue'
import type { Stop, StudyContext } from '../types'
const props = defineProps<{ stop: Stop; context: StudyContext }>()
const facts = computed(() => props.context.facts.filter(fact => props.stop.study?.fact_ids.includes(fact.id)))
const policy = computed(() => props.stop.study ? props.context.policies[props.stop.study.place_id] : null)
const sources = computed(() => {
  const ids = new Set([...facts.value.flatMap(fact => fact.source_ids), ...(policy.value?.source_ids || [])])
  return props.context.sources.filter(source => ids.has(source.id))
})
</script>

<template>
  <div v-if="stop.study" class="study-stop-content">
    <section v-if="stop.study.why_selected" class="study-reason"><h5>为什么来这里</h5><p>{{ stop.study.why_selected }}</p></section>
    <section v-if="stop.study.observation_task" class="study-observation"><h5><PencilLine :size="14" />现场观察</h5><p>{{ stop.study.observation_task }}</p></section>
    <footer class="study-evidence">
      <div class="evidence-heading"><strong>资料依据 · {{ sources.length }}篇</strong><SourceDrawer :context="context" :fact-ids="stop.study.fact_ids" :source-ids="policy?.source_ids" :stop="stop" trigger-label="查看引用" /></div>
      <ul v-if="sources.length" class="evidence-sources"><li v-for="source in sources" :key="source.id"><span>{{ source.title }}</span><small v-if="source.publisher || source.publication_date">{{ [source.publisher, source.publication_date].filter(Boolean).join(' · ') }}</small></li></ul>
      <p v-else class="evidence-empty">本版本未保存来源信息</p>
    </footer>
  </div>
</template>

<style scoped>
.study-reason { margin-top: 19px; padding-top: 17px; border-top: 1px solid #edf1eb; }
h5 { display: flex; align-items: center; gap: 6px; margin: 0 0 7px; color: #426754; font-size: 12px; font-weight: 600; }
.study-reason p, .study-observation p { margin: 0; color: #5d7165; font-size: 14px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
.study-observation { margin-top: 18px; padding: 14px; border-radius: 10px; background: #f0f6ee; }
.study-evidence { display: block; width: auto; margin: 20px 0 0; padding: 16px 0 0; border-top: 1px solid #edf1eb; }
.evidence-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.evidence-heading strong { color: #718579; font-size: 11px; font-weight: 500; }
.evidence-sources { display: grid; gap: 10px; list-style: none; margin: 10px 0 0; padding: 0; }
.evidence-sources span { display: block; color: #60796b; font-size: 12px; line-height: 1.75; overflow-wrap: anywhere; }
.evidence-sources small { display: block; color: #8a998f; font-size: 11px; line-height: 1.7; margin-top: 2px; overflow-wrap: anywhere; }
.evidence-empty { font-size: 12px; color: #8a998f; margin-top: 8px; }
</style>
