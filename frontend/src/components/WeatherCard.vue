<script setup lang="ts">
import { computed } from 'vue'
import { Cloud, CloudRain, Snowflake, Sun, Wind, Info, Shirt, Backpack, Landmark, Eye, RefreshCw } from 'lucide-vue-next'
import type { WeatherSnapshot } from '../types'
import { weatherAdvice } from '../weatherAdvice'

const props = defineProps<{ weather?: WeatherSnapshot | null; date: string; city?: string; retrying?: boolean; retryDisabled?: boolean; refreshed?: boolean; retryError?: string }>()
defineEmits<{ retry: [] }>()
const day = computed(() => props.weather?.days.find(item => item.date === props.date))
const available = computed(() => ['available', 'partial'].includes(props.weather?.status || '') && day.value?.status === 'available')
const advice = computed(() => weatherAdvice(props.weather, props.date))
const adviceIcons = { clothing: Shirt, gear: Backpack, activity: Landmark, wind: Wind, visibility: Eye }
const icon = computed(() => {
  const text = (day.value?.day_weather || '') + (day.value?.night_weather || '')
  return text.includes('雪') ? Snowflake : text.includes('雨') ? CloudRain : text.includes('晴') ? Sun : Cloud
})
const notice = computed(() => !props.weather ? '此版本未保存天气信息' :
  props.weather.status === 'unavailable' || day.value?.status === 'unavailable'
    ? '天气暂不可用，本次未据此调整' : '该日期暂无预报')
function timeLabel(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '未知' : new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(parsed)
}
</script>

<template>
  <section class="weather-card card" aria-label="当日天气预报">
    <template v-if="available && day && weather">
      <div class="weather-main"><span class="weather-icon"><component :is="icon" :size="25" /></span>
        <div><div class="weather-title"><strong>{{ weather.city || weather.requested_city }} · {{ day.day_weather || '白天天气未知' }}</strong><span>{{ date.slice(5).replace('-', ' / ') }}</span></div>
          <p v-if="weather.provider === 'qweather'" class="weather-temperature">最高 {{ day.temp_max ?? '—' }}°C <span>／</span> 最低 {{ day.temp_min ?? '—' }}°C<span v-if="day.night_weather"> · 夜间{{ day.night_weather }}</span></p>
          <p v-else class="weather-temperature">白天 {{ day.day_temp ?? '—' }}°C <span>／</span> 夜间 {{ day.night_temp ?? '—' }}°C<span v-if="day.night_weather"> · {{ day.night_weather }}</span></p>
        </div>
      </div>
      <div class="weather-details"><span v-if="day.day_wind || day.day_power"><Wind :size="14" />白天 {{ day.day_wind || '风向未知' }}<template v-if="day.day_power"> {{ day.day_power }}级</template></span>
        <span v-if="day.night_wind || day.night_power">夜间 {{ day.night_wind || '风向未知' }}<template v-if="day.night_power"> {{ day.night_power }}级</template></span></div>
      <section v-if="advice.length" class="weather-advice" aria-label="当天出行建议">
        <div class="weather-advice-heading"><strong>当天出行建议</strong><span>结合已保存的日级预报</span></div>
        <ul><li v-for="item in advice" :key="item.kind"><component :is="adviceIcons[item.kind]" :size="17" /><div><strong>{{ item.title }}</strong><p>{{ item.text }}</p></div></li></ul>
        <p class="weather-advice-note">穿衣可按个人体感调整；室内场所的开放与预约请另查出行参考。</p>
      </section>
      <p class="weather-source">{{ weather.provider === 'qweather' ? '和风天气' : '高德预报' }} · 发布于 {{ weather.issued_at ? timeLabel(weather.issued_at) : '未知' }}（北京时间）<span v-if="weather.from_cache"> · 30分钟内缓存</span></p>
      <p class="weather-caption">日级预报供安排参考，出发前请再核对；不代表场馆开放情况。</p>
    </template>
    <div v-else class="weather-empty"><Info :size="18" /><div><strong><template v-if="city">{{ city }} · </template>{{ notice }}</strong><p>{{ weather?.failure_reason || (weather?.status === 'not_covered' && weather.message ? weather.message : weather ? '保留原有行程安排，未用其他日期的天气代替。' : '历史方案仍可正常查看与校验。') }}</p>
      <button v-if="city" class="secondary-button weather-retry" :disabled="retrying || retryDisabled" @click="$emit('retry')"><RefreshCw :size="14" :class="{ spin: retrying }" />{{ retrying ? '正在查询天气…' : weather?.status === 'unavailable' ? '重试天气' : '查询最新天气' }}</button>
      <p v-if="city" class="weather-caption">只查询天气，不调用大模型，无需重新生成整份行程。</p>
    </div></div>
    <p v-if="retryError" class="weather-retry-error" role="alert">{{ retryError }}</p>
    <p v-if="refreshed" class="weather-caption">本次重查结果仅供查看；已保存行程和原天气快照保持不变，行程未据此重新调整。</p>
  </section>
</template>

<style scoped>
.weather-card { padding: 18px 22px; margin-bottom: 18px; background: linear-gradient(115deg, #f0f7f4, #fff); }
.weather-main { display: flex; align-items: center; gap: 13px; }
.weather-icon { width: 46px; height: 46px; border-radius: 14px; background: #e1eee7; color: #34775e; display: grid; place-items: center; flex-shrink: 0; }
.weather-title { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.weather-title strong { font-size: 15px; color: #264c41; }
.weather-title > span { font-size: 12px; color: #71867d; }
.weather-temperature { margin: 5px 0 0; font-size: 13px; color: #46655a; }
.weather-temperature > span { color: #7c8c84; }
.weather-details { display: flex; flex-wrap: wrap; gap: 7px 18px; margin-top: 11px; color: #657b71; font-size: 12px; }
.weather-details span { display: inline-flex; align-items: center; gap: 5px; }
.weather-advice { border-top: 1px solid #e0ebe5; margin-top: 14px; padding-top: 14px; }
.weather-advice-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.weather-advice-heading > strong { color: #376953; font-size: 13px; font-weight: 600; }
.weather-advice-heading > span { color: #81928a; font-size: 11px; }
.weather-advice ul { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap: 12px 20px; margin: 13px 0 0; padding: 0; list-style: none; }
.weather-advice li { display: flex; align-items: flex-start; gap: 9px; min-width: 0; }
.weather-advice li > svg { flex-shrink: 0; margin-top: 3px; color: #63917c; }
.weather-advice li strong { font-size: 12px; color: #507560; font-weight: 600; }
.weather-advice li p { margin: 4px 0 0; color: #6d8377; font-size: 12px; line-height: 1.85; overflow-wrap: anywhere; }
.weather-advice-note { font-size: 11px; color: #839389; line-height: 1.75; margin: 12px 0 0; }
.weather-source, .weather-caption { font-size: 11px; line-height: 1.7; color: #7c8b83; margin: 8px 0 0; }
.weather-caption { margin-top: 2px; }
.weather-empty { display: flex; gap: 10px; align-items: flex-start; color: #7d8b84; }
.weather-empty strong { font-size: 13px; font-weight: 500; }
.weather-empty p { font-size: 12px; margin: 5px 0 0; line-height: 1.6; }
.weather-retry { margin-top: 11px; padding: 8px 12px; font-size: 12px; }
.weather-retry-error { color: #a45d32; font-size: 12px; line-height: 1.8; margin-top: 10px; }
@media (max-width: 600px) { .weather-card { padding: 15px; } .weather-temperature { line-height: 1.8; } }
</style>
