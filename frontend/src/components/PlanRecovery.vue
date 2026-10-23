<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, RefreshCw, Sparkles } from 'lucide-vue-next'
import type { Plan } from '../types'
import { planRecovery } from '../planRecovery'

const props = defineProps<{ plan: Plan; busy: boolean; checking: boolean }>()
defineEmits<{ refresh: []; revise: [] }>()
const recovery = computed(() => planRecovery(props.plan))
</script>

<template>
  <section v-if="recovery.lookupCount || recovery.planningCount" class="recovery-card card" aria-label="行程问题与重试">
    <div class="recovery-title"><CircleAlert :size="18" /><strong>这份行程还有需要处理的地方</strong></div>
    <div v-if="recovery.lookupCount" class="recovery-row">
      <p><strong>地点或路线尚未查到</strong><span>{{ recovery.configuration ? '检测到服务权限或配额问题，请先核对配置再查询。' : '可能与网络、限流或地点匹配有关，单凭缺失结果无法确定原因。' }}</span></p>
      <button v-if="!plan.request.study" class="secondary-button" :disabled="busy || checking" @click="$emit('refresh')"><RefreshCw :size="15" />重新查询地点与路线</button>
      <button v-else class="secondary-button" :disabled="busy || checking" @click="$emit('revise')"><Sparkles :size="15" />重新核对研学安排</button>
    </div>
    <div v-if="recovery.planningCount" class="recovery-row">
      <p><strong>城市、时间或费用安排需要调整</strong><span>这类问题需要调整行程内容，重新查询网络资料不一定能解决。</span></p>
      <button class="secondary-button" :disabled="busy || checking" @click="$emit('revise')"><Sparkles :size="15" />让 AI 调整行程</button>
    </div>
    <p class="recovery-note">查询地点与路线不调用大模型；{{ plan.request.study ? '研学调整会调用模型并保存新版本。' : '普通旅行的 AI 调整先展示约束预览，确认后才保存新版本。' }}天气可在天气卡片单独重查。</p>
  </section>
</template>

<style scoped>
.recovery-card { margin: 0 0 18px; padding: 18px 22px; border-color: #eadbc6; background: #fffdf8; }
.recovery-title { display: flex; align-items: center; gap: 9px; color: #946b40; font-size: 14px; }
.recovery-title > svg { flex-shrink: 0; }
.recovery-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 18px; margin-top: 16px; }
.recovery-row p { flex: 1 1 240px; line-height: 1.8; margin: 0; }
.recovery-row p strong { display: block; color: #526d5d; font-size: 13px; }
.recovery-row p span, .recovery-note { font-size: 12px; color: #7a877d; line-height: 1.8; }
.recovery-row button { min-width: 0; white-space: normal; }
.recovery-note { margin: 15px 0 0; }
@media(max-width:520px) { .recovery-card { padding: 16px; } .recovery-row button { width: 100%; justify-content: center; } }
</style>
