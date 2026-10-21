import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ComponentPublicInstance, ObjectDirective } from 'vue'

const transientAnimations = new Set<Animation>()
function stopTransitions() { transientAnimations.forEach(animation => animation.cancel()); transientAnimations.clear() }

// Only animate the existing timeline; map, chat and photo state stay mounted.
export const timelineEnter: ObjectDirective<HTMLElement, unknown> = {
  updated(element, binding) {
    if (binding.value === binding.oldValue || document.documentElement.dataset.roamMotion !== 'on' || !element.animate) return
    const animation = element.animate([{ opacity: .35 }, { opacity: 1 }], { duration: 180, easing: 'ease-out' })
    transientAnimations.add(animation)
    animation.finished.catch(() => {}).finally(() => transientAnimations.delete(animation))
  },
}

export function usePageMotion() {
  const motionRequested = ref(new URLSearchParams(window.location.search).get('motion') !== 'off')
  const media = window.matchMedia('(prefers-reduced-motion: reduce)')
  const reduced = ref(media.matches), pageVisible = ref(!document.hidden), heroVisible = ref(false)
  const hero = ref<HTMLElement>(), heroEntered = ref(false), introPlaying = ref(false)
  const motion = computed(() => motionRequested.value && !reduced.value)
  const ambientRunning = computed(() => motion.value && pageVisible.value && heroVisible.value)
  let observer: IntersectionObserver | undefined, introTimer: ReturnType<typeof setTimeout> | undefined
  const element = document.documentElement
  const originalAttributes = Object.fromEntries(['roamTheme', 'roamMotion', 'roamAmbient'].map(key => [key, element.dataset[key]]))

  function updateUrl() {
    const url = new URL(window.location.href)
    if (!motionRequested.value) url.searchParams.set('motion', 'off'); else url.searchParams.delete('motion')
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
  }
  function toggleMotion() { motionRequested.value = !motionRequested.value; updateUrl() }
  function syncUrl() { motionRequested.value = new URLSearchParams(window.location.search).get('motion') !== 'off' }
  function updateReduced() { reduced.value = media.matches }
  function updateVisibility() { pageVisible.value = !document.hidden; if (pageVisible.value) measureHero() }
  function setHero(value: Element | ComponentPublicInstance | null) { hero.value = value instanceof HTMLElement ? value : undefined }
  function measureHero() {
    if (!hero.value) { heroVisible.value = false; return }
    const bounds = hero.value.getBoundingClientRect()
    heroVisible.value = bounds.width > 0 && bounds.height > 0 && bounds.bottom > 0 && bounds.top < window.innerHeight && bounds.right > 0 && bounds.left < window.innerWidth
  }

  watch([motion, ambientRunning], () => {
    element.dataset.roamTheme = 'city'
    element.dataset.roamMotion = motion.value ? 'on' : 'off'
    element.dataset.roamAmbient = ambientRunning.value ? 'running' : 'paused'
    if (!motion.value) { stopTransitions(); introPlaying.value = false; clearTimeout(introTimer) }
  }, { immediate: true, flush: 'sync' })
  watch(hero, value => {
    observer?.disconnect(); heroVisible.value = false
    if (!value) return
    // Start visible scenery immediately, rather than waiting for the first observer delivery.
    measureHero()
    if (typeof IntersectionObserver === 'undefined') return
    observer = new IntersectionObserver(entries => { heroVisible.value = entries.some(entry => entry.isIntersecting) }, { threshold: .05 })
    observer.observe(value)
  }, { flush: 'post' })
  watch([motion, pageVisible, heroVisible], () => {
    if (ambientRunning.value && !heroEntered.value) {
      heroEntered.value = true; introPlaying.value = true
      introTimer = setTimeout(() => { introPlaying.value = false }, 550)
    }
  })
  onMounted(() => {
    media.addEventListener('change', updateReduced)
    document.addEventListener('visibilitychange', updateVisibility)
    window.addEventListener('popstate', syncUrl)
  })
  onBeforeUnmount(() => {
    observer?.disconnect(); clearTimeout(introTimer); stopTransitions()
    media.removeEventListener('change', updateReduced)
    document.removeEventListener('visibilitychange', updateVisibility)
    window.removeEventListener('popstate', syncUrl)
    Object.entries(originalAttributes).forEach(([key, value]) => { if (value === undefined) delete element.dataset[key]; else element.dataset[key] = value })
  })
  return { motionRequested, reduced, motion, setHero, introPlaying, toggleMotion }
}
