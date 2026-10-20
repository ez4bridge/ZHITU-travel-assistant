<script setup lang="ts">
import type { GuideSource } from '../types'
const sources = defineModel<GuideSource[]>({ default: () => [] })
const mode = defineModel<'prefer' | 'inspiration'>('mode', { default: 'prefer' })
</script>

<template>
  <details class="guide-input">
    <summary>参考攻略（可选，最多 3 篇）</summary>
    <p class="subtle-note">粘贴小红书或其他攻略正文即可，结果会列出原文摘录、对应活动及未采纳原因。价格、营业和预约仍需核实。</p>
    <label v-if="sources.length" class="field"><span>攻略使用方式</span><select v-model="mode"><option value="prefer">优先按攻略地点与顺序规划</option><option value="inspiration">仅作游玩灵感</option></select></label>
    <div v-for="(source, i) in sources" :key="i" class="guide-source">
      <label class="field"><span>攻略 {{ i + 1 }} 正文</span><textarea v-model="source.text" required rows="5" maxlength="6000" /></label>
      <button type="button" class="text-button" @click="sources.splice(i, 1)">移除攻略</button>
    </div>
    <button v-if="sources.length < 3" type="button" class="text-button" @click="sources.push({ title: `参考攻略 ${sources.length + 1}`, text: '' })">添加一篇攻略</button>
  </details>
</template>
