<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { MapPin, Map, AlertCircle, RefreshCw } from 'lucide-vue-next'
import { api } from '../api'
import type { MapRegion, Stop, RouteLeg } from '../types'

const props = defineProps<{ stops: Stop[]; routes: RouteLeg[]; selectedId: string; cities?: string[]; canRefresh?: boolean; canRevise?: boolean; busy?: boolean }>()
const emit = defineEmits<{ select: [id: string]; retry: []; revise: [] }>()
const host = ref<HTMLDivElement>()
const loading = ref(true), error = ref('')
const regions = ref<MapRegion[]>([]), regionLoading = ref(false)
const hasPoints = computed(() => props.stops.some(stop => stop.location_status === 'verified' && stop.location))
const regionLabel = computed(() => regions.value.map(region => region.city).join('、'))
const regionCache = new globalThis.Map<string, MapRegion>()
let regionController: AbortController | undefined, regionSequence = 0
// The SDK is loaded at runtime from Amap; these handles deliberately remain opaque.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let map: any, sdk: any
let mounted = true
const overlays: unknown[] = []

function draw() {
  if (!map || !sdk) return
  if (overlays.length) map.remove(overlays.splice(0))
  props.routes.filter(r => hasPoints.value && r.available && r.polyline.length).forEach(route => {
    const line = new sdk.Polyline({ path: route.polyline, strokeColor: '#147b71', strokeWeight: 6,
      strokeOpacity: .85, lineJoin: 'round', showDir: true, zIndex: 30 })
    overlays.push(line)
  })
  props.stops.forEach((stop, index) => {
    if (stop.location_status !== 'verified' || !stop.location) return
    const marker = new sdk.Marker({ position: stop.location, title: stop.matched_name || stop.name,
      anchor: 'bottom-center', content: `<div class="map-pin ${stop.id === props.selectedId ? 'is-selected' : ''}"><span>${index + 1}</span></div>`, zIndex: 100 })
    marker.on('click', () => emit('select', stop.id))
    overlays.push(marker)
  })
  if (!hasPoints.value) {
    regions.value.forEach(region => {
      const label = document.createElement('div')
      label.className = 'map-region-label'; label.textContent = region.city + ' · 区域位置'
      overlays.push(new sdk.Marker({ position: region.center, content: label,
        title: '行政区大致位置，并非活动坐标', anchor: 'center', zIndex: 50 }))
    })
  }
  map.add(overlays)
  if (hasPoints.value && overlays.length) map.setFitView(overlays, false, [60, 60, 60, 60], 15)
  else if (regions.value.length === 1) map.setZoomAndCenter(regions.value[0]!.level === 'province' ? 8 : 10, regions.value[0]!.center)
  else if (regions.value.length > 1) map.setFitView(overlays, false, [110, 50, 70, 50], 10)
  else map.setZoomAndCenter(4, [104.1954, 35.8617])
}

watch(() => hasPoints.value ? '' : [...new Set(props.cities?.length ? props.cities : props.stops.map(stop => stop.city).filter(Boolean))].join('\n'), async names => {
  regionController?.abort()
  const sequence = ++regionSequence
  regions.value = []; regionLoading.value = Boolean(names); draw()
  if (!names) return
  regionController = new AbortController()
  const signal = regionController.signal
  try {
    const results = await Promise.all(names.split('\n').slice(0, 5).map(async city => {
      const cached = regionCache.get(city)
      if (cached) return cached
      try {
        const result = await api<MapRegion>('/api/map-region?city=' + encodeURIComponent(city), { signal })
        if (result.status === 'available' && result.center && !signal.aborted) {
          if (regionCache.size >= 32) regionCache.delete(regionCache.keys().next().value!)
          regionCache.set(city, result)
        }
        return result
      } catch { return null }
    }))
    if (!mounted || signal.aborted || sequence !== regionSequence) return
    regions.value = results.filter((region): region is MapRegion => Boolean(region?.status === 'available' && region.center))
  } finally {
    if (sequence === regionSequence && mounted) { regionLoading.value = false; draw() }
  }
}, { immediate: true })

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
    const first = props.stops.find(stop => stop.location)?.location
    map = new sdk.Map(host.value, { zoom: first ? 11 : 4, center: first || [104.1954, 35.8617], viewMode: '2D', mapStyle: 'amap://styles/normal' })
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
onBeforeUnmount(() => { mounted = false; regionController?.abort(); map?.destroy() })
</script>

<template>
  <div class="map-component">
    <div ref="host" class="map-canvas" :aria-label="hasPoints ? '高德真实路线地图' : '目的地区域参考地图'"></div>
    <div v-if="loading || error" class="map-message">
      <Map v-if="loading" :size="32" /><AlertCircle v-else :size="32" />
      <strong>{{ loading ? '正在铺开城市地图…' : '地图暂时无法显示' }}</strong>
      <span v-if="error">{{ error }}</span>
    </div>
    <div v-if="!loading && !error && !hasPoints" class="map-region-notice" role="status">
      <MapPin :size="17" />
      <div><strong>{{ regionLoading ? '正在查询目的地区域位置…' : regionLabel ? regionLabel + ' · 大致位置' : '暂未取得区域位置，显示全国概览' }}</strong>
      <span>{{ stops.length ? '具体活动尚未定位；区域位置仅供参考，不作为路线起终点。' : '当天暂无活动地点；区域位置仅供参考，跨城交通见上方安排。' }}</span>
      <div class="map-region-actions"><button v-if="canRefresh" class="text-button" :disabled="busy" @click="emit('retry')"><RefreshCw :size="13" />重新查询地点</button>
      <button v-if="canRevise" class="text-button" :disabled="busy" @click="emit('revise')">让 AI 调整行程</button></div></div>
    </div>
    <div v-if="hasPoints" class="map-legend"><span></span> 高德已选交通路线 <small>GCJ-02</small></div>
    <div v-else-if="regions.length" class="map-region-source">高德行政区中心 · 非景点坐标</div>
  </div>
</template>

<style scoped>
.map-region-notice { position: absolute; top: 12px; left: 12px; right: 12px; z-index: 120; display: flex; align-items: flex-start; gap: 8px; padding: 11px 13px; border: 1px solid #e9ddbf; border-radius: 9px; background: #fffbf2f5; color: #8b754f; box-shadow: 0 2px 8px #425a3410; }
.map-region-notice > svg { flex-shrink: 0; margin-top: 2px; }
.map-region-notice strong { display: block; font-size: 12px; line-height: 1.7; font-weight: 500; }
.map-region-notice span { display: block; font-size: 11px; line-height: 1.7; margin-top: 3px; }
.map-region-actions { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 6px; }
.map-region-actions button { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; padding: 2px 0; }
.map-region-source { position: absolute; bottom: 27px; left: 12px; padding: 6px 8px; border-radius: 5px; background: #ffffffed; color: #788a79; font-size: 10px; pointer-events: none; }
.map-component :deep(.map-region-label) { padding: 7px 12px; border: 1px dashed #538978; border-radius: 18px; background: #fffffff2; box-shadow: 0 2px 8px #29412726; color: #416b5c; font: 12px system-ui; white-space: nowrap; }
</style>
