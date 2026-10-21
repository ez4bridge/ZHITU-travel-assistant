<script setup lang="ts">
import { computed } from 'vue'
import { Info, ExternalLink } from 'lucide-vue-next'
import type { StudyPolicy, StudySource, Stop } from '../types'

const props = defineProps<{ policy: StudyPolicy; sources: StudySource[]; stop?: Stop; date: string }>()
// Unknown or out-of-date information is not evidence of a closure. This also
// applies to old saved plans without regenerating or rewriting their snapshots.
const closed = computed(() => props.sources.length > 0 &&
  ['documented_closed', 'not_visitable'].includes(props.policy.status))
</script>

<template>
  <aside v-if="closed" class="visit-advisory" aria-label="收录资料参观提醒">
    <strong><Info :size="14" />收录资料提醒 <span>{{ date }}</span></strong>
    <p class="visit-notice">{{ policy.status === 'documented_closed' ? '收录公告记载该日闭馆。' : '收录资料明确记载该地点不对外开放。' }}</p>
    <p v-if="policy.note">{{ policy.note }}</p>
    <p v-if="policy.windows.length">收录开放时段：{{ policy.windows.map(window => window.join('–')).join('、') }}<template v-if="policy.last_entry">；{{ policy.last_entry }}停止入场</template></p>
    <p v-else-if="policy.last_entry">收录停止入场时间：{{ policy.last_entry }}</p>
    <a v-for="source in sources.filter(source => /^https?:\/\//i.test(source.url))" :key="source.id" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }} <ExternalLink :size="11" /></a>
    <small>来自行程保存的本地资料，不产生联网或模型调用。请以最新公告为准。</small>
  </aside>
</template>

<style scoped>
.visit-advisory { background: #faf5e9; border: 1px solid #efe3c8; border-radius: 9px; padding: 11px 12px; margin: 11px 0; color: #89734c; }
.visit-advisory strong { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 11px; font-weight: 500; }
.visit-advisory strong span { margin-left: auto; font-size: 10px; color: #a28d69; }
.visit-advisory p { font-size: 11px; line-height: 1.85; margin: 7px 0; overflow-wrap: anywhere; }
.visit-advisory .visit-notice { color: #a76e36; }
.visit-advisory a { display: block; font-size: 10px; line-height: 1.8; color: #887751; margin-top: 6px; overflow-wrap: anywhere; text-decoration: underline; text-underline-offset: 2px; }
.visit-advisory a svg { vertical-align: middle; }
.visit-advisory small { display: block; font-size: 10px; line-height: 1.8; margin-top: 8px; color: #a28d69; }
@media(max-width:520px) { .visit-advisory p { font-size: 12px; } }
</style>
