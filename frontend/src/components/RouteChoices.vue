<script setup lang="ts">
import type { Mode, RouteLeg } from '../types'
defineProps<{ route: RouteLeg; disabled: boolean }>()
const emit = defineEmits<{ select: [mode: Mode] }>()
const names: Record<Mode, string> = { walking: '步行', driving: '自驾', transit: '公交 / 地铁', bicycling: '骑行', taxi: '打车' }
</script>

<template>
  <div class="route-choices">
    <p v-if="route.stationary">原地活动，无需地点间转移</p>
    <template v-else>
      <p><strong>{{ names[route.mode] }}</strong> · {{ route.available ? `${Math.ceil((route.duration_s || 0) / 60)} 分钟` : '路线未核实' }}</p>
      <p class="subtle-note">{{ route.note }}</p>
      <p v-if="route.selection_reason" class="subtle-note">{{ route.selection_reason }}</p>
      <p v-if="route.cost_estimate" class="subtle-note">约 ¥{{ route.cost_estimate.low }}–{{ route.cost_estimate.high }} / {{ route.cost_estimate.basis === 'vehicle' ? '辆' : '人' }} · {{ route.cost_estimate.method }}</p>
      <details v-if="route.alternatives?.length">
        <summary>比较交通方式与注意事项</summary>
        <div v-for="option in route.alternatives" :key="option.mode" class="route-choice">
          <div><strong>{{ names[option.mode] }}</strong><span>{{ option.available ? `${Math.ceil((option.duration_s || 0) / 60)} 分钟 · ${((option.distance_m || 0) / 1000).toFixed(1)} km` : '暂无可用路线' }}</span>
            <small v-if="option.cost_estimate">约 ¥{{ option.cost_estimate.low }}–{{ option.cost_estimate.high }} / {{ option.cost_estimate.basis === 'vehicle' ? '辆' : '人' }} · {{ option.cost_estimate.method }}</small>
            <small v-else-if="option.fare_yuan != null">高德票价参考 ¥{{ option.fare_yuan }} / 人</small>
            <small v-else-if="option.available && option.mode !== 'walking'">请更新路线以补齐费用参考</small>
            <small v-if="option.cost_estimate">{{ option.cost_estimate.assumptions }} <a v-if="option.cost_estimate.source_url" :href="option.cost_estimate.source_url" target="_blank" rel="noopener noreferrer">费用依据</a></small>
            <small v-if="option.provider_endpoint">路线来源：高德 {{ option.provider_endpoint }}<template v-if="option.mode === 'taxi'">（打车使用驾车路网，不含候车）</template></small>
            <small v-else-if="option.available">历史路线未记录接口来源，更新后可查看</small>
            <small>{{ option.instructions?.join(' → ') }}</small><small>{{ option.note }}</small>
          </div>
          <button type="button" class="text-button" :disabled="disabled || !option.available || route.mode === option.mode" @click="emit('select', option.mode)">{{ route.mode === option.mode ? '已选' : '选用' }}</button>
        </div>
      </details>
    </template>
  </div>
</template>
