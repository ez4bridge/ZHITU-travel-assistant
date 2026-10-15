<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { MapPin, Map, AlertCircle } from 'lucide-vue-next'
import { api } from '../api'
import type { Stop, RouteLeg } from '../types'

const props = defineProps<{ stops: Stop[]; routes: RouteLeg[]; selectedId: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const host = ref<HTMLDivElement>()
const loading = ref(true), error = ref('')
// The SDK is loaded at runtime from Amap; these handles deliberately remain opaque.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let map: any, sdk: any
let mounted = true
const overlays: unknown[] = []

function draw() {
  if (!map || !sdk) return
  if (overlays.length) map.remove(overlays.splice(0))
  props.routes.filter(r => r.available && r.polyline.length).forEach(route => {
    const line = new sdk.Polyline({ path: route.polyline, strokeColor: '#147b71', strokeWeight: 6,
      strokeOpacity: .85, lineJoin: 'round', showDir: true, zIndex: 30 })
    overlays.push(line)
  })
  props.stops.forEach((stop, index) => {
    if (!stop.location) return
    const marker = new sdk.Marker({ position: stop.location, title: stop.matched_name || stop.name,
      anchor: 'bottom-center', content: `<div class="map-pin ${stop.id === props.selectedId ? 'is-selected' : ''}"><span>${index + 1}</span></div>`, zIndex: 100 })
    marker.on('click', () => emit('select', stop.id))
    overlays.push(marker)
  })
  map.add(overlays)
  if (overlays.length) map.setFitView(overlays, false, [60, 60, 60, 60], 15)
}

onMounted(async () => {
  try {
    const config = await api<{ key: string; security_proxy: string }>('/api/map-config')
    if (!config.key) throw new Error('地图 Web Key 尚未配置；行程与路线信息仍可查看。')
    const browser = window as unknown as Record<string, any>
    if (!browser.AMap) {
      browser._AMapSecurityConfig = { serviceHost: window.location.origin + config.security_proxy }
      await new Promise<void>((resolve, reject) => {
        const script = document.createElement('script')
        const timer = window.setTimeout(() => reject(new Error('地图加载超时，请检查网络后刷新页面。')), 20000)
        browser.roamMapReady = () => { clearTimeout(timer); resolve() }
        script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.key)}&callback=roamMapReady`
        script.onerror = () => { clearTimeout(timer); reject(new Error('无法加载高德地图，请检查网络。')) }
        document.head.appendChild(script)
      })
    }
    if (!mounted || !host.value) return
    sdk = browser.AMap
    if (!sdk) throw new Error('地图 SDK 未成功加载')
    map = new sdk.Map(host.value, { zoom: 11, center: [121.4737, 31.2304], viewMode: '2D', mapStyle: 'amap://styles/normal' })
    map.on('error', () => { error.value = '地图鉴权失败，请核对 Web Key 与安全密钥。' })
    draw()
  } catch (e) { error.value = e instanceof Error ? e.message : '地图加载失败' }
  finally { loading.value = false }
})
watch(() => [props.stops, props.routes], draw)
watch(() => props.selectedId, () => {
  draw()
  const stop = props.stops.find(s => s.id === props.selectedId)
  if (map && stop?.location) map.setCenter(stop.location)
})
onBeforeUnmount(() => { mounted = false; map?.destroy() })
</script>

<template>
  <div class="map-component">
    <div ref="host" class="map-canvas" aria-label="高德真实路线地图"></div>
    <div v-if="loading || error" class="map-message">
      <Map v-if="loading" :size="32" /><AlertCircle v-else :size="32" />
      <strong>{{ loading ? '正在铺开城市地图…' : '地图暂时无法显示' }}</strong>
      <span v-if="error">{{ error }}</span>
    </div>
    <div v-if="!loading && !error && !stops.some(s => s.location)" class="map-no-poi">
      <MapPin :size="18" /> 地点尚未定位，暂时没有路线可展示
    </div>
    <div class="map-legend"><span></span> 高德{{ routes[0]?.mode === 'driving' ? '驾车' : '步行' }}路线 <small>GCJ-02</small></div>
  </div>
</template>
