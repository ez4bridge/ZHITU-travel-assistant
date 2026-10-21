<script setup lang="ts">
import { computed } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import type { QAResult } from '../types'
import SourceDrawer from './SourceDrawer.vue'

const props = defineProps<{ result: QAResult }>()
const text = computed(() => 'paragraphs' in props.result ? props.result : null)
const photo = computed(() => 'answer' in props.result ? props.result : null)
const factIds = computed(() => [...new Set(text.value?.paragraphs.flatMap(p => p.fact_ids) || [])])
function safeUrl(value: string) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? value : null }
  catch { return null }
}
</script>

<template>
  <div class="qa-answer qa-chat-answer">
    <template v-if="text"><p v-if="text.message">{{ text.message }}</p><p v-for="(paragraph, i) in text.paragraphs" :key="i">{{ paragraph.text }}</p><SourceDrawer v-if="text.context" :context="text.context" :fact-ids="factIds" /></template>
    <template v-if="photo">
      <span class="qa-answer-label">照片里的线索</span><p>{{ photo.answer.visible_summary }}</p>
      <template v-if="photo.answer.history.length"><span class="qa-answer-label">背后的故事</span><p v-for="(claim, i) in photo.answer.history" :key="i">{{ claim.text }} <small>{{ claim.source_ids.map(id => '[' + id + ']').join(' ') }}</small></p></template>
      <p v-if="photo.answer.observation_tip" class="qa-observation">{{ photo.answer.observation_tip }}</p><p v-if="photo.answer.unknowns.length" class="qa-photo-unknown">尚不能确认：{{ photo.answer.unknowns.join('；') }}</p>
      <div v-if="photo.sources.length" class="qa-photo-sources"><span class="qa-answer-label">本轮参考资料</span><template v-for="source in photo.sources" :key="source.id"><a v-if="safeUrl(source.url)" :href="safeUrl(source.url)!" target="_blank" rel="noopener noreferrer">[{{ source.id }}] {{ source.title }} <ExternalLink :size="12" /></a><span v-else>[{{ source.id }}] {{ source.title }}</span></template></div>
    </template>
    <details v-if="result.usage" class="qa-usage"><summary>查看本轮用量</summary><span>{{ result.usage.attempted_calls }}次模型调用 · {{ result.usage.total_tokens ?? '未完整返回' }} token<span v-if="result.usage.usage_missing_calls">部分调用未返回用量</span></span><span v-if="result.usage.prompt_tokens != null">输入 {{ result.usage.prompt_tokens }} · 输出 {{ result.usage.completion_tokens ?? '未知' }}</span><span>搜索 {{ result.usage.search_calls ?? 0 }} 次 · 抓取 {{ result.usage.fetch_calls ?? 0 }} 次 · 网页缓存 {{ result.usage.web_cache_hits ?? 0 }} 条</span><span v-if="result.usage.memory_chars != null">本轮携带 {{ result.usage.history_turns }} 轮历史 · {{ result.usage.memory_chars }} 字符 · {{ result.usage.seconds }} 秒</span></details>
  </div>
</template>
