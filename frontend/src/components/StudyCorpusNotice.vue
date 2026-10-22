<script setup lang="ts">
import { computed } from 'vue'
import { BookOpen, ChevronDown } from 'lucide-vue-next'
import type { StudyCatalog } from '../types'

const props = defineProps<{ catalog: StudyCatalog | null }>()
const documents = computed(() => props.catalog?.documents ?? [])
</script>

<template>
  <section class="study-corpus-notice" aria-labelledby="study-corpus-title">
    <div class="corpus-notice-heading">
      <div class="corpus-notice-title"><BookOpen :size="21" aria-hidden="true" /><h3 id="study-corpus-title">历史研学 · RAG 功能演示（仅支持上海）</h3></div>
    </div>
    <p class="corpus-notice-description">本模块采用 RAG（检索增强生成），依据资料原文检索线索、规划研学并回答问题。本 Demo 仅使用上海图书馆开放数据平台按公开许可提供的地点资料。</p>
    <a class="corpus-license-note" href="https://data1.library.sh.cn/index" target="_blank" rel="noopener noreferrer">资料许可：CC BY-NC-SA 2.0，特别注明者除外</a>
    <details class="corpus-document-list">
      <summary><span>查看全部研学资料<span v-if="catalog?.ready" class="corpus-document-count">{{ catalog.source_count }} 篇</span></span><ChevronDown :size="14" class="corpus-list-chevron" aria-hidden="true" /></summary>
      <ol v-if="documents.length">
        <li v-for="document in documents" :key="document.id">{{ document.title }}</li>
      </ol>
      <p v-else class="corpus-document-empty">{{ catalog && !catalog.ready ? catalog.message : '资料目录暂未加载，请稍后刷新页面。' }}</p>
    </details>
  </section>
</template>

<style scoped>
.study-corpus-notice{margin-bottom:20px;padding:22px 24px;border:1px solid #c7dfd3;border-left:4px solid #147b71;border-radius:14px;background:#f2f8f4;box-shadow:0 4px 18px #315c4210}
.corpus-notice-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.corpus-notice-title{display:flex;align-items:center;gap:9px;color:#147b71;min-width:0}
.corpus-notice-title h3{margin:0;font-size:17px;line-height:1.5;font-weight:650;color:#244e43;overflow-wrap:anywhere}
.corpus-notice-title>svg{flex-shrink:0}
.corpus-notice-description{margin:12px 0 6px;max-width:860px;font-size:13px;line-height:1.9;color:#49695b}
.corpus-license-note{display:inline-block;font-size:11px;line-height:1.8;color:#627d6c;text-decoration:underline;text-underline-offset:3px}
.corpus-document-list{margin-top:15px;border-top:1px solid #d8e7dd;padding-top:11px}
.corpus-document-list summary{display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;color:#466f5c;font-size:12px;line-height:1.8;list-style:none}
.corpus-document-list summary::-webkit-details-marker{display:none}
.corpus-document-list summary:focus-visible,.corpus-license-note:focus-visible{outline:2px solid #147b71;outline-offset:4px;border-radius:3px}
.corpus-document-count{margin-left:9px;color:#738878;font-size:11px}
.corpus-list-chevron{flex-shrink:0}
.corpus-document-list[open] .corpus-list-chevron{transform:rotate(180deg)}
.corpus-document-list ol{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 24px;max-height:230px;overflow:auto;overscroll-behavior:contain;margin:12px 0 0;padding:0 8px 2px 0;scrollbar-width:thin;scrollbar-color:#b8d1c2 transparent}
.corpus-document-list li{margin-left:20px;padding:2px 0;font-size:11px;line-height:1.85;color:#637a69;overflow-wrap:anywhere}
.corpus-document-list li::marker{color:#809786}
.corpus-document-empty{font-size:11px;line-height:1.8;color:#6d8071;margin:10px 0 0}
@media(max-width:650px){.study-corpus-notice{padding:18px 16px}.corpus-notice-title h3{font-size:16px}.corpus-notice-description{font-size:12px}.corpus-document-list ol{grid-template-columns:1fr;max-height:240px}}
</style>
