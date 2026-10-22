import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export function beijingMinute(now = new Date()) {
  const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(now)
  const get = (key: string) => parts.find(part => part.type === key)!.value
  return { date: `${get('year')}-${get('month')}-${get('day')}`, time: `${get('hour')}:${get('minute')}` }
}

export function useDepartureClock() {
  const clock = ref(beijingMinute(new Date(Date.now() + 60000)))
  let timer: ReturnType<typeof setInterval> | undefined
  onMounted(() => { timer = setInterval(() => { clock.value = beijingMinute(new Date(Date.now() + 60000)) }, 1000) })
  onBeforeUnmount(() => clearInterval(timer))
  return { today: computed(() => clock.value.date), earliest: clock }
}

export function futureDeparture(date: string, time: string) {
  const now = beijingMinute()
  return `${date}T${time}` > `${now.date}T${now.time}`
}

export function defaultDepartureDate() {
  return beijingMinute(new Date(Date.now() + 86400000)).date
}
