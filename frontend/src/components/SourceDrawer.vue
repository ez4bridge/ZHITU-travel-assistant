<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { BookOpen, ExternalLink, X } from 'lucide-vue-next'
import { placeTerms, sourceParts } from '../sourceHighlights'
import type { Stop, StudyContext } from '../types'

const props = defineProps<{ context: StudyContext; factIds?: string[]; sourceIds?: string[]; stop?: Stop; triggerLabel?: string }>()
const open = ref(false), dialog = ref<HTMLElement>(), trigger = ref<HTMLButtonElement>()
const facts = computed(() => props.context.facts.filter(fact => !props.factIds || props.factIds.includes(fact.id)))
const passages = computed(() => {
  const terms = placeTerms(props.stop)
  return facts.value.map(fact => ({ fact, parts: sourceParts(fact.text, terms) }))
})
const hasLocationMatch = computed(() => passages.value.some(passage => passage.parts.some(part => part.highlighted)))
const sources = computed(() => {
  const ids = new Set([...facts.value.flatMap(fact => fact.source_ids), ...(props.sourceIds || [])])
  return props.context.sources.filter(source => ids.has(source.id))
})
function safeUrl(url: string) {
  try {
    const parsed = new URL(url)
    return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password ? url : null
  } catch { return null }
}
async function show() {
  open.value = true
  await nextTick()
  dialog.value?.focus({ preventScroll: true })
  dialog.value?.querySelector('mark')?.scrollIntoView({ block: 'center', behavior: 'instant' })
}
function close() { open.value = false; trigger.value?.focus({ preventScroll: true }) }
function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialog.value) return
  const controls = Array.from(dialog.value.querySelectorAll<HTMLElement>('button, a[href]'))
  const target = event.shiftKey ? controls.at(-1) : controls[0]
  if (document.activeElement === dialog.value || (event.shiftKey && document.activeElement === controls[0]) || (!event.shiftKey && document.activeElement === controls.at(-1))) {
    event.preventDefault(); target?.focus()
  }
}
</script>

<template>
  <button ref="trigger" type="button" class="source-button" :aria-label="stop ? '查看' + stop.name + '的引用资料' : undefined" @click="show"><BookOpen :size="14" />{{ triggerLabel || '查看资料出处' }}</button>
  <Teleport to="body">
    <Transition name="appearance-modal">
    <div v-if="open" class="modal-backdrop source-backdrop" @click.self="close" @keydown.esc="close">
      <section ref="dialog" class="source-drawer card" role="dialog" aria-modal="true" aria-label="资料与出处" tabindex="-1" @keydown="trapFocus">
        <div class="mini-heading"><BookOpen :size="20" /><h3>每条线索，都有出处</h3><button class="icon-button" aria-label="关闭资料出处" @click="close"><X :size="19" /></button></div>
        <p v-if="stop" class="source-place">当前地点 · {{ stop.name }}</p>
        <p v-if="hasLocationMatch" class="source-match-note">已高亮地点名称或地址，可结合完整上下文核对。</p>
        <p class="source-intro">{{ context.retrieval_mode === 'raw_text' ? '以下为本次检索引用的原文段落。历史信息与参观条件需结合上下文和公告日期理解。' : '以下为该历史版本保存的资料摘要，保留生成时的内容与出处。' }}</p>
        <article v-for="passage in passages" :key="passage.fact.id" class="source-fact"><h4>{{ passage.fact.title }}</h4><p class="source-passage"><template v-for="(part, index) in passage.parts" :key="index"><mark v-if="part.highlighted">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></p><small v-if="passage.fact.start_char != null">原文字符位置：{{ passage.fact.start_char }}—{{ passage.fact.end_char }}</small></article>
        <div class="source-list"><h4>资料来源</h4><article v-for="source in sources" :key="source.id"><strong>{{ source.title }}</strong><p>{{ source.publisher }}<span v-if="source.publication_date"> · {{ source.publication_date }}</span></p><p v-if="source.original_source_mentions.length">原始资料署名：{{ source.original_source_mentions.join('；') }}</p><small v-if="source.retrieved_at">本地获取：{{ source.retrieved_at.slice(0, 10) }}</small><a v-if="safeUrl(source.url)" :href="safeUrl(source.url)!" target="_blank" rel="noopener noreferrer">阅读原网页 <ExternalLink :size="12" /></a></article></div>
      </section>
    </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.source-button { display: inline-flex; align-items: center; gap: 5px; margin: 0; padding: 5px 0; font-size: 12px; color: #147b71; white-space: nowrap; }
.source-button:focus-visible { outline: 2px solid #75a995; outline-offset: 4px; border-radius: 3px; }
.source-drawer { width: min(600px, 100%); padding: 0 26px 28px; overscroll-behavior: contain; }
.source-drawer > .mini-heading { top: 0; margin: 0; padding: 20px 0; border-bottom: 1px solid #e6ece5; }
.source-drawer .mini-heading h3 { font-size: 17px; }
.source-place { margin-top: 18px; font-size: 13px; line-height: 1.8; color: #426754; overflow-wrap: anywhere; }
.source-match-note { margin-top: 7px; font-size: 12px; line-height: 1.8; color: #789080; }
.source-intro { margin: 12px 0 18px; font-size: 12px; }
.source-fact h4 { font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; }
.source-fact .source-passage { white-space: pre-wrap; font-size: 14px; line-height: 2; overflow-wrap: anywhere; }
.source-passage mark { background: #e3efd0; color: #365f41; border-radius: 3px; padding: 2px 0; scroll-margin: 95px; }
.source-list a { width: fit-content; }
@media(max-width:520px) { .source-drawer { padding: 0 20px 24px; } .source-drawer > .mini-heading { padding: 18px 0; } .source-drawer .mini-heading h3 { font-size: 16px; } }
</style>
