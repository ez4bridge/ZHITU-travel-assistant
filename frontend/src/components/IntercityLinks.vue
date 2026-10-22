<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ fromCity: string; toCity: string; travelDate: string }>()
const message = ref('')
const query = computed(() => `${props.fromCity} → ${props.toCity}；出发日期 ${props.travelDate}；请在携程核对起降机场、班次、行李和含税全价。`)
async function copyQuery() {
  try {
    await navigator.clipboard.writeText(query.value)
    message.value = '已复制城市和日期，请在携程查询页粘贴或填写。'
  } catch {
    message.value = '未取得剪贴板权限，可直接选中下方文字复制。'
  }
}
</script>

<template>
  <div class="flight-query-links">
    <p>机票尚未接入实时数据。当前时间和费用是规划估算，不代表可售航班、余票、含税成交价或已订票。</p>
    <div class="flight-query-actions"><a href="https://flights.ctrip.com/online/channel/domestic" target="_blank" rel="noopener noreferrer">去携程查询国内机票</a><button type="button" class="text-button" @click="copyQuery">复制城市与日期</button></div>
    <p class="flight-query-text">{{ query }}</p><p v-if="message" role="status">{{ message }}</p>
  </div>
</template>

<style scoped>
.flight-query-links { margin-top:10px; padding:12px; border:1px solid #dce6df; border-radius:10px; background:#f5f8f4; font-size:12px; line-height:1.7; overflow-wrap:anywhere; }
.flight-query-links p { margin:0 0 8px; }
.flight-query-actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
.flight-query-actions a { color:#287361; text-decoration:underline; }
.flight-query-text { margin-top:10px!important; user-select:text; color:#677c6c; }
</style>
