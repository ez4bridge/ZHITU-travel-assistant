<script setup lang="ts">
import { Landmark } from 'lucide-vue-next'
import type { PlanningGuideSource, GuideDecision, Stop } from '../types'
const props = defineProps<{ guides: PlanningGuideSource[]; decisions: GuideDecision[]; stops: Stop[] }>()
const labels = { adopted: '已采纳', adjusted: '调整后采纳', not_used: '未落实' }
const label = (decision: GuideDecision) => decision.status === 'not_used' && /不能确认已采纳|没有提供可核对/.test(decision.reason) ? '未能核对' : labels[decision.status]
const stopName = (id: string) => props.stops.find(stop => stop.id === id)?.name || ''
</script>

<template>
  <div v-if="guides.length" class="travel-tips guide-evidence">
    <Landmark :size="17" /><div><strong>攻略采纳与调整</strong>
      <p v-if="!decisions.length">这份历史方案没有逐项采纳记录，无法仅凭摘要确认攻略已落实；可按攻略优先重新规划。</p>
      <article v-for="(decision, i) in decisions" :key="i">
        <p><strong>{{ label(decision) }}</strong> · {{ guides[decision.source_index]?.title }}</p>
        <blockquote>“{{ decision.excerpt }}”</blockquote>
        <p v-if="decision.stop_ids.length">对应活动：{{ decision.stop_ids.map(stopName).filter(Boolean).join('、') }}</p>
        <p>{{ decision.reason }}</p>
        <a v-if="guides[decision.source_index]?.url" :href="guides[decision.source_index]!.url" target="_blank" rel="noopener noreferrer">查看攻略来源</a>
      </article>
      <small>摘录与活动关联经程序核对；取舍原因是规划解释，不代表攻略事实已获官方确认。</small>
    </div>
  </div>
</template>
