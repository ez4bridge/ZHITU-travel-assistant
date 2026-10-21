import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Ref } from 'vue'

export type SkyTraffic = 'plane'
export const skyCycleMs = 50_000
export const skyEvents: { kind: SkyTraffic; start: number; end: number }[] = [{ kind: 'plane', start: 8_000, end: 32_000 }]

// Only one low-frequency timeout. Pausing saves active elapsed time, not wall time.
export function useSceneTraffic(motion: Ref<boolean>, visible: Ref<boolean>, desktop: Ref<boolean>) {
  const active = computed(() => motion.value && visible.value && desktop.value)
  const traffic = ref<SkyTraffic | null>(null), flightProgressMs = ref(0), flightId = ref(0)
  let elapsed = 0, startedAt = 0, running = false, timer: ReturnType<typeof setTimeout> | undefined
  function tick() {
    const phase = (elapsed + performance.now() - startedAt) % skyCycleMs
    const current = skyEvents.find(event => phase >= event.start && phase < event.end)
    traffic.value = current?.kind || null
    flightProgressMs.value = current ? phase - current.start : 0
    flightId.value++
    const next = current?.end ?? skyEvents.find(event => event.start > phase)?.start ?? skyCycleMs
    timer = setTimeout(tick, Math.max(1, next - phase))
  }
  function pause() {
    clearTimeout(timer)
    if (running) elapsed = (elapsed + performance.now() - startedAt) % skyCycleMs
    running = false
  }
  function resume() {
    pause()
    if (!active.value) return
    startedAt = performance.now(); running = true; tick()
  }
  const stop = watch(active, resume, { flush: 'sync' })
  onMounted(resume)
  onBeforeUnmount(() => { stop(); pause() })
  return { traffic, flightProgressMs, flightId }
}
