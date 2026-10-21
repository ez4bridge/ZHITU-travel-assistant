<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, toRef } from 'vue'
import { useSceneTraffic } from './sceneTraffic'
import JourneyCity from './JourneyCity.vue'

const props = defineProps<{ motion: boolean }>()
const pageVisible = ref(!document.hidden)
const desktopQuery = window.matchMedia('(min-width: 1280px)')
const desktop = ref(desktopQuery.matches)
const { traffic, flightProgressMs, flightId } = useSceneTraffic(toRef(props, 'motion'), pageVisible, desktop)
function syncVisibility() { pageVisible.value = !document.hidden }
function syncWidth() { desktop.value = desktopQuery.matches }
onMounted(() => {
  document.addEventListener('visibilitychange', syncVisibility)
  desktopQuery.addEventListener('change', syncWidth)
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', syncVisibility)
  desktopQuery.removeEventListener('change', syncWidth)
})
</script>

<template>
  <div class="journey-backdrop" :data-page-visible="pageVisible" :data-motion="motion" aria-hidden="true">
    <div class="journey-sky"></div>
    <svg class="world-cloud world-cloud-one world-loop" viewBox="0 0 240 96" fill="none" focusable="false"><path d="M28 80C2 80 3 49 28 46C25 19 57 6 77 24C92-5 142-2 154 30C186 13 216 36 209 58C240 56 246 83 217 83H28Z" fill="#fff" /></svg>
    <svg class="world-cloud world-cloud-two world-loop" viewBox="0 0 240 96" fill="none" focusable="false"><path d="M20 80C0 74 2 48 29 47C31 22 62 15 79 30C95 0 140 6 149 35C177 20 210 35 207 58C239 57 245 82 216 84H20Z" fill="#e8efe1" /></svg>

    <div class="journey-landscape">
      <svg class="journey-hills" viewBox="0 0 1920 360" preserveAspectRatio="none" fill="none" focusable="false">
        <defs>
          <linearGradient id="journey-mountain-mist" x1="960" y1="50" x2="960" y2="360" gradientUnits="userSpaceOnUse"><stop stop-color="#e2ebe0" /><stop offset="1" stop-color="#edf1e5" /></linearGradient>
          <linearGradient id="journey-mountain-near" x1="960" y1="110" x2="960" y2="360" gradientUnits="userSpaceOnUse"><stop stop-color="#cfdfd0" /><stop offset="1" stop-color="#dce7d6" /></linearGradient>
        </defs>
        <path d="M-40 190L31 128L74 142L154 49L204 91L235 80L330 156L385 123L493 220Q712 278 941 270Q1174 279 1336 205L1423 137L1466 148L1559 61L1596 87L1630 81L1738 171L1801 131L1960 222V380H-40Z" fill="url(#journey-mountain-mist)" />
        <path d="M154 49L139 120L184 108L214 147L263 164L204 91Z M1559 61L1531 139L1583 121L1613 154L1694 180L1630 81L1596 87Z" fill="#d2e0d4" opacity=".65" />
        <path d="M-40 248Q-3 226 41 191L90 204L168 146L197 161L234 147Q303 185 371 232Q452 199 517 247Q672 296 918 292Q1147 299 1315 244L1403 191L1440 203L1511 154L1565 182L1612 157Q1692 180 1758 218L1825 178L1960 266V380H-40Z" fill="url(#journey-mountain-near)" />
        <path d="M168 146L157 204L196 191L226 218L291 219L234 147L197 161Z M1511 154L1495 213L1531 203L1574 228L1649 239L1612 157L1565 182Z" fill="#bfd4c2" opacity=".65" />
        <path d="M-40 302Q77 239 178 278Q280 225 387 282Q504 267 613 310Q902 333 1212 309Q1351 249 1449 277Q1551 229 1645 281Q1801 239 1960 305V380H-40Z" fill="#c3d9c5" />
        <g class="landscape-detail" stroke="#edf0df" stroke-width="2" stroke-linecap="round" opacity=".7">
          <path d="M74 142L102 136L154 49L186 82M1423 137L1457 154M1559 61L1596 87L1630 81M91 204L168 146L197 161M1440 203L1473 192L1511 154" />
          <path d="M27 303Q98 279 157 295M307 279Q342 273 367 286M1416 287Q1473 263 1526 286M1699 292Q1769 275 1829 291" />
        </g>
        <g class="landscape-detail" fill="#b2cdb8" opacity=".6"><path d="M52 284L58 265L65 284M63 285L71 258L80 285M82 281L87 267L93 281M1810 280L1817 257L1825 280M1826 282L1836 251L1847 282M1844 283L1852 265L1860 283" /></g>
      </svg>
      <JourneyCity class="world-city city-left" side="left" />
      <JourneyCity class="world-city city-right" side="right" />

      <svg class="world-trees trees-left" viewBox="0 0 220 180" fill="none" focusable="false">
        <g class="world-tree world-loop"><path d="M50 167V78" stroke="#8bad97" stroke-width="6" /><circle cx="49" cy="70" r="34" fill="#b3cca9" /><circle cx="30" cy="82" r="22" fill="#bdd5b8" /></g>
        <g class="world-tree tree-offset world-loop"><path d="M151 169V94" stroke="#8bad97" stroke-width="5" /><path d="M111 114L151 23L192 114Z" fill="#bdcfaa" /><path d="M120 91L151 23L183 91Z" fill="#cad9b4" /></g>
      </svg>
      <svg class="world-trees trees-right" viewBox="0 0 220 180" fill="none" focusable="false">
        <g class="world-tree world-loop"><path d="M58 168V79" stroke="#8bad97" stroke-width="5" /><circle cx="58" cy="69" r="34" fill="#bbd2ad" /><circle cx="79" cy="84" r="24" fill="#c7dabb" /></g>
        <g class="world-tree tree-offset world-loop"><path d="M159 170V109" stroke="#8bad97" stroke-width="5" /><circle cx="159" cy="92" r="28" fill="#a8c7ad" /></g>
      </svg>
    </div>

    <div class="journey-road"><div class="road-marking"></div></div>
    <div class="journey-water">
      <svg class="world-waterlines world-loop" viewBox="0 0 1920 120" preserveAspectRatio="none" fill="none" focusable="false"><path d="M-100 26Q100 5 300 26T700 26T1100 26T1500 26T2000 26M-50 75Q150 54 350 75T750 75T1150 75T1550 75T2050 75" stroke="#eaf1dc" stroke-width="3" opacity=".7" /><path d="M70 104H210M400 50H530M920 105H1130M1510 53H1690" stroke="#d6e9d7" stroke-width="3" stroke-linecap="round" /></svg>
    </div>

    <div class="world-vehicle world-transport world-loop">
      <svg class="vehicle-body world-loop" viewBox="0 0 168 72" fill="none" focusable="false">
        <defs>
          <linearGradient id="journey-sedan-paint" x1="85" y1="12" x2="85" y2="57" gradientUnits="userSpaceOnUse"><stop stop-color="#b6d7c6" /><stop offset=".6" stop-color="#91bda8" /><stop offset="1" stop-color="#79a991" /></linearGradient>
          <linearGradient id="journey-sedan-glass" x1="78" y1="15" x2="93" y2="33" gradientUnits="userSpaceOnUse"><stop stop-color="#edf6e9" /><stop offset="1" stop-color="#c2ddcf" /></linearGradient>
        </defs>
        <g class="journey-sedan">
          <ellipse cx="85" cy="64" rx="73" ry="4" fill="#729a8226" />
          <path d="M12 36L43 29L60 15Q65 11 73 11H101Q110 11 117 18L132 31L153 36Q161 38 161 47V55H139C138 35 114 35 113 55H49C48 35 24 35 23 55H13Q8 55 8 50V42Q8 38 12 36Z" fill="url(#journey-sedan-paint)" stroke="#79a78f" stroke-width="1.2" />
          <path d="M48 29L64 17Q68 15 74 15H83V30Z" fill="url(#journey-sedan-glass)" />
          <path d="M88 15H100Q106 15 111 20L122 30H88Z" fill="url(#journey-sedan-glass)" />
          <path d="M54 28L67 18H75M93 18H99Q104 18 108 22" stroke="#fff9" stroke-width="1.5" stroke-linecap="round" />
          <path d="M45 32H129M14 40Q80 36 151 40" stroke="#d9ead4" stroke-width="1.4" stroke-linecap="round" />
          <path d="M84 33V51M123 34L127 41M49 50H111" stroke="#76a68d" stroke-width="1.1" stroke-linecap="round" />
          <path d="M70 36H77M103 36H110" stroke="#f0f3df" stroke-width="2" stroke-linecap="round" />
          <path d="M119 29L127 28L130 32L123 34Z" fill="#7da88e" />
          <path d="M151 38L158 40V44H149Z" fill="#f6edc8" /><path d="M10 40L17 39V45H10Z" fill="#caa28b" />
          <path d="M142 49H158M13 50H21" stroke="#69977f" stroke-width="2" stroke-linecap="round" /><path d="M54 55H108" stroke="#6c9a80" stroke-width="2" stroke-linecap="round" />
        </g>
        <g v-for="x in [36, 126]" :key="x" :transform="`translate(${x} 55)`"><g class="vehicle-wheel world-loop"><circle r="10" fill="#5c786b" /><circle r="7" fill="#e0e8d8" /><circle r="5.5" stroke="#aac1ae" /><path d="M0-5.5V5.5M-5.2-1.7L5.2 1.7M-3.2 4.5L3.2-4.5" stroke="#a0b9a5" stroke-width="1.3" stroke-linecap="round" /><circle r="1.8" fill="#759a82" /></g></g>
      </svg>
    </div>
    <div class="world-boat world-transport world-loop">
      <svg class="boat-bob world-loop" viewBox="0 0 220 100" fill="none" focusable="false">
        <defs><linearGradient id="journey-boat-hull" x1="110" y1="57" x2="110" y2="82" gradientUnits="userSpaceOnUse"><stop stop-color="#a8cab8" /><stop offset="1" stop-color="#7ea794" /></linearGradient><linearGradient id="journey-boat-glass" x1="100" y1="27" x2="112" y2="53" gradientUnits="userSpaceOnUse"><stop stop-color="#e0eee1" /><stop offset="1" stop-color="#b0d1c1" /></linearGradient></defs>
        <g class="journey-tour-boat">
          <path d="M21 59L41 57H199L182 78Q117 87 52 78Q34 76 21 59Z" fill="url(#journey-boat-hull)" stroke="#7fa894" stroke-width="1.2" />
          <path d="M29 64H192M47 73Q112 80 183 73" stroke="#e8e8ce" stroke-width="2" />
          <path d="M47 55V37Q47 31 54 31H166Q174 31 179 40L185 55Z" fill="#eee6cb" stroke="#b1bda1" />
          <path d="M44 31H173" stroke="#9db49e" stroke-width="4" stroke-linecap="round" />
          <path d="M76 28V17Q76 13 81 13H140Q145 13 145 18V28Z" fill="#eee9d3" stroke="#b4c4ad" /><path d="M71 13H149" stroke="#a2baa5" stroke-width="3" stroke-linecap="round" />
          <rect x="83" y="18" width="20" height="10" rx="2" fill="url(#journey-boat-glass)" /><rect x="108" y="18" width="20" height="10" rx="2" fill="url(#journey-boat-glass)" /><path d="M134 18H140V28H134Z" fill="#bbd6c7" />
          <rect v-for="n in 5" :key="n" :x="39 + n * 22" y="37" width="16" height="16" rx="2" fill="url(#journey-boat-glass)" />
          <path d="M54 38L62 38M76 38H84M98 38H106M120 38H128" stroke="#faf9e9" stroke-width="1.3" stroke-linecap="round" />
          <path d="M31 57V47H44M187 56V47H199M166 27V20H182V48" stroke="#a5bca7" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /><path d="M172 24V33M178 24V39" stroke="#b3c6ad" stroke-width="1.2" />
          <circle cx="158" cy="45" r="6" fill="#d7b38c" /><circle cx="158" cy="45" r="3.3" fill="#f2edda" /><path d="M154 41L162 49M162 41L154 49" stroke="#f1ead4" stroke-width="1.5" />
          <path d="M110 12V4" stroke="#a2b49d" stroke-width="1.5" /><path d="M111 4H123L119 10H111Z" fill="#cbb58b" /><rect x="51" y="62" width="11" height="3" rx="1.5" fill="#769c88" /><rect x="168" y="62" width="11" height="3" rx="1.5" fill="#769c88" />
        </g>
        <path d="M18 88Q69 82 110 87T208 86M43 95H114M169 94H210" stroke="#ecf3df" stroke-width="2.5" stroke-linecap="round" opacity=".8" />
      </svg>
    </div>

    <div v-if="traffic === 'plane'" :key="'plane-' + flightId" class="world-plane-flight world-sky-traffic" :style="{ animationDelay: -flightProgressMs + 'ms' }">
      <svg viewBox="0 0 210 90" fill="none" focusable="false">
        <path d="M75 37L104 13H118L105 39Z" fill="#c1d6c8" />
        <path d="M8 46C9 40 25 35 39 35H136L159 8H174L162 36H179L191 24H201L192 44L197 51H132L118 55H34C20 55 8 52 8 46Z" fill="#e7eee0" stroke="#a2c0af" stroke-width="1.5" />
        <path d="M12 48H182" stroke="#abc9b6" stroke-width="3" />
        <path d="M92 43L126 78H109L64 48Z" fill="#b1cebd" stroke="#9cbaa8" />
        <path d="M155 35L163 13H170L164 35Z" fill="#9ebeaa" />
        <path d="M25 39L36 38L33 44H21Z" fill="#98b6a6" />
        <rect v-for="n in 8" :key="n" :x="35 + n * 11" y="39" width="5" height="5" rx="2" fill="#9ebaac" />
        <path d="M89 61H104L111 67H88Z" fill="#91b09e" /><path d="M164 42H185" stroke="#edf2e2" stroke-width="2" />
      </svg>
    </div>
    <div class="journey-reading-veil"></div>
  </div>
</template>

<style scoped>
.journey-backdrop { --transport-opacity: .82; position: fixed; inset: 0; z-index: -1; pointer-events: none; overflow: hidden; background: #f4f7ee; }
.journey-sky { position: absolute; inset: 0; background: linear-gradient(180deg, #e5f0e7 0%, #f8f9f0 53%, #eef3e6 82%); }
.journey-landscape { position: absolute; inset: 0 0 18vh; }
.journey-hills { position: absolute; bottom: 0; width: 100%; height: 38vh; opacity: .85; }
.world-city { position: absolute; bottom: 0; width: clamp(320px, 32vw, 620px); max-height: 32vh; height: auto; opacity: .72; }
.city-left { left: 1vw; }.city-right { right: .5vw; }
.world-trees { position: absolute; bottom: 0; width: clamp(125px, 14vw, 220px); max-height: 19vh; height: auto; opacity: .8; }
.trees-left { left: 0; }.trees-right { right: 1vw; }
.world-tree { transform-box: fill-box; transform-origin: 50% 100%; }
.journey-road { position: absolute; bottom: 12vh; height: 6vh; left: 0; right: 0; background: #d4e0d1; border-top: 4px solid #bdcfb8; border-bottom: 5px solid #bad2c0; }
.road-marking { position: absolute; height: 2px; top: 53%; left: 0; right: 0; background: repeating-linear-gradient(90deg, #f2f3dc 0 25px, transparent 25px 61px); opacity: .6; }
.journey-water { position: absolute; bottom: 0; left: 0; right: 0; height: 12vh; background: linear-gradient(180deg, #bfdacf, #d6e7d8); }
.world-waterlines { position: absolute; inset: 0; width: 110%; height: 100%; left: -5%; }
.world-cloud { position: absolute; width: clamp(120px, 13vw, 230px); height: auto; opacity: .85; }
.world-cloud-one { top: 14vh; left: 14vw; }.world-cloud-two { top: 26vh; left: 70vw; width: clamp(160px, 18vw, 310px); opacity: .7; }
.world-transport, .world-sky-traffic { display: none; position: absolute; left: 0; }
.world-vehicle { bottom: calc(12vh + 8px); width: clamp(104px, 9vw, 144px); }
.world-boat { bottom: 1.5vh; width: clamp(145px, 14vw, 220px); }
.world-transport > svg, .world-sky-traffic > svg { width: 100%; height: auto; display: block; }
.vehicle-wheel { transform-box: fill-box; transform-origin: center; }
.world-transport, .world-plane-flight { z-index: 1; opacity: var(--transport-opacity); }
.world-plane-flight { width: clamp(118px, 10vw, 168px); top: 18vh; }
/* Strongest veil follows the reading area, leaving river and edge scenery visible. */
.journey-reading-veil { position: absolute; inset: 0; background: radial-gradient(ellipse 64% 58% at 50% 34%, #f8f9f3f2 15%, #f8f9f3bd 53%, #f8f9f34d 78%, #f8f9f300 100%); }

html[data-roam-motion="on"] .world-cloud-one { left: 0; animation: world-cloud-cross 70s linear -17s infinite; }
html[data-roam-motion="on"] .world-cloud-two { left: 0; animation: world-cloud-cross 95s linear -65s infinite reverse; }
html[data-roam-motion="on"] .world-tree { animation: world-tree-sway 5s ease-in-out -2s infinite alternate; }
html[data-roam-motion="on"] .tree-offset { animation-delay: -4s; animation-duration: 6s; }
html[data-roam-motion="on"] .world-waterlines { animation: world-water-flow 9s ease-in-out -3s infinite alternate; }
html[data-roam-motion="on"] .world-vehicle { display: block; animation: world-vehicle-pass 28s linear -4s infinite; }
html[data-roam-motion="on"] .vehicle-body { animation: world-vehicle-bob 1.8s ease-in-out infinite alternate; }
html[data-roam-motion="on"] .vehicle-wheel { animation: world-wheel-turn .9s linear infinite; }
html[data-roam-motion="on"] .world-boat { display: block; animation: world-boat-pass 48s linear -12s infinite; }
html[data-roam-motion="on"] .boat-bob { animation: world-boat-bob 3s ease-in-out -1s infinite alternate; }
html[data-roam-motion="on"] .world-plane-flight { display: block; animation: world-plane-pass 24s linear both; }
.journey-backdrop[data-page-visible="false"] .world-loop,
.journey-backdrop[data-page-visible="false"] .world-sky-traffic { animation-play-state: paused; }
@keyframes world-cloud-cross { from { transform: translateX(-320px); } to { transform: translateX(calc(100vw + 320px)); } }
@keyframes world-tree-sway { from { transform: rotate(-2deg); } to { transform: rotate(2.5deg); } }
@keyframes world-water-flow { from { transform: translateX(-12px); opacity: .5; } to { transform: translateX(18px); opacity: .9; } }
@keyframes world-vehicle-pass { 0% { transform: translateX(-180px); } 78.5714%, 100% { transform: translateX(calc(100vw + 180px)); } }
@keyframes world-wheel-turn { to { transform: rotate(360deg); } }
@keyframes world-vehicle-bob { from { transform: translateY(0); } to { transform: translateY(-.7px); } }
@keyframes world-boat-pass { 0% { transform: translateX(calc(100vw + 240px)); } 83.3333%, 100% { transform: translateX(-240px); } }
@keyframes world-boat-bob { from { transform: translateY(-2px) rotate(-.5deg); } to { transform: translateY(2px) rotate(.5deg); } }
@keyframes world-plane-pass { from { transform: translate(calc(100vw + 190px), 24px) rotate(-2deg); } to { transform: translate(-190px, -20px) rotate(-2deg); } }

@media (min-width: 768px) and (max-width: 1279px) {
  .landscape-detail, .world-sky-traffic { display: none !important; }
  .world-city { opacity: .5; width: 32vw; }
  .world-trees { width: 16vw; }
  .world-vehicle { width: 106px; }.world-boat { width: 148px; }
  .journey-reading-veil { background: radial-gradient(ellipse 75% 65% at 50% 30%, #f8f9f3ed 20%, #f8f9f399 65%, #f8f9f300); }
}
@media (max-width: 767px) {
  .world-cloud, .world-city, .world-trees, .road-marking, .world-transport, .world-sky-traffic { display: none !important; }
  .journey-backdrop .world-loop { animation: none !important; }
  .journey-hills { height: 14vh; opacity: .45; }
  .journey-road { background: #e7ecde; border-top-width: 2px; border-bottom-width: 2px; opacity: .55; }
  .journey-water { opacity: .55; }.world-waterlines { opacity: .35; }
  .journey-reading-veil { background: linear-gradient(180deg, #f8f9f355 0%, #f8f9f3b3 67%, #f8f9f355); }
}
@media (prefers-reduced-motion: reduce) {
  .journey-backdrop .world-loop, .journey-backdrop .world-sky-traffic { animation: none !important; }
  .world-transport, .world-sky-traffic { display: none !important; }
}
@media print { .journey-backdrop { display: none; } }
</style>
