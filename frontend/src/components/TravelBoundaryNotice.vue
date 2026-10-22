<script setup lang="ts">
import { computed } from 'vue'
import { ArrowLeft, Compass, Rocket, Sparkles, X } from 'lucide-vue-next'
const props = defineProps<{ city: string }>()
defineEmits<{ edit: []; close: [] }>()
// Presentation only: the backend still verifies every destination through Amap.
const cosmic = computed(() => {
  const name = props.city.normalize('NFKC').trim()
  const chinese = /地球(?:以外|之外|外)|地外|外星|太空|宇宙|星际|星際|星系|银河|銀河|空间站|空間站|轨道|軌道|星云|星雲|黑洞|虫洞|蟲洞|火星|月球|月亮|月面|月背|冥王星|木星|土星|金星|水星|天王星|海王星|太阳|太陽|仙女座|半人马|半人馬|比邻星|比鄰星|天狼星|织女星|織女星|北极星|北極星|开普勒|開普勒|系外|小行星|彗星|恒星|恆星|行星|星球|中子星|超新星|(?:木|土|天|海|火|冥)卫[一二三四五六七八九十\d]+/
  const english = /\b(?:mars|moon|lunar|jupiter|saturn|mercury|venus|neptune|uranus|pluto|sun|solar|galaxy|galaxies|andromeda|space|iss|titan|europa|proxima|kepler|exoplanet|asteroid|comet|nebula|wormhole|black\s+hole)\b/i
  return chinese.test(name) || english.test(name)
})
</script>

<template>
  <div class="boundary-notice" role="status" aria-labelledby="boundary-title">
    <div class="boundary-icon" aria-hidden="true"><component :is="cosmic ? Rocket : Compass" :size="25" /><Sparkles class="boundary-spark" :size="12" /></div>
    <div class="boundary-body">
      <span class="boundary-tag">当前可达 · 中国境内</span>
      <h3 id="boundary-title">{{ cosmic ? '这趟有点远，飞船还没准备好' : '旅行雷达还没找到这一站' }}</h3>
      <p v-if="cosmic">「{{ city }}」先放进宇宙旅行愿望单。知途暂时只支持国内旅行，星际航线还没开通呢。</p>
      <p v-else>知途暂时只支持国内旅行，境外和虚构目的地还在愿望单里。如果「{{ city }}」是国内地点，请试试城市或区县全称。</p>
      <button type="button" class="boundary-edit" @click="$emit('edit')"><ArrowLeft :size="14" />换个国内目的地，继续出发</button>
    </div>
    <button type="button" class="boundary-close" aria-label="关闭旅行范围提示" @click="$emit('close')"><X :size="17" /></button>
  </div>
</template>

<style scoped>
.boundary-notice { display: flex; align-items: flex-start; gap: 15px; position: relative; padding: 21px; margin-bottom: 16px; border: 1px solid #d8e7de; border-radius: 16px; background: linear-gradient(115deg, #f0f7f1, #fffaf1); color: #355e52; }
.boundary-icon { position: relative; display: grid; place-items: center; width: 48px; height: 48px; flex-shrink: 0; border: 1px dashed #a6c5b5; border-radius: 50%; color: #4b8270; background: #ffffffb3; }
.boundary-spark { position: absolute; right: -3px; top: -3px; color: #b58b47; }
.boundary-body { min-width: 0; padding-right: 14px; }
.boundary-tag { font-size: 10px; letter-spacing: .7px; color: #829879; }
.boundary-body h3 { font-size: 15px; line-height: 1.6; margin: 7px 0; font-weight: 600; }
.boundary-body p { font-size: 12px; line-height: 1.9; color: #6d8072; overflow-wrap: anywhere; }
.boundary-edit { display: inline-flex; align-items: center; gap: 6px; margin-top: 13px; padding: 6px 0; color: #357568; font-size: 12px; text-align: left; }
.boundary-edit:hover { text-decoration: underline; text-underline-offset: 4px; }
.boundary-edit:focus-visible, .boundary-close:focus-visible { outline: 2px solid #357568; outline-offset: 3px; border-radius: 4px; }
.boundary-close { position: absolute; right: 10px; top: 10px; display: grid; place-items: center; padding: 5px; color: #8b9b8d; }
@media (max-width: 600px) { .boundary-notice { padding: 19px 15px; gap: 11px; } .boundary-icon { width: 37px; height: 37px; } .boundary-body h3 { font-size: 14px; } }
</style>
