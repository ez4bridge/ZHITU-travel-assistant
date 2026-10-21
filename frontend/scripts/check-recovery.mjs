import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { harness } from './check-appearance.mjs'

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, plugins: [vue()],
  server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
})
try {
  const { default: Recovery } = await server.ssrLoadModule('/src/components/PlanRecovery.vue')
  const { default: Weather } = await server.ssrLoadModule('/src/components/WeatherCard.vue')
  const { planRecovery } = await server.ssrLoadModule('/src/planRecovery.ts')
  const base = { id: 'original', request: { study: null }, routes: [], itinerary: { days: [{ stops: [{ id: 'a', location_behavior: 'poi' }] }] }, validation: { errors: [] } }
  for (const [label, errors, expected] of [
    ['lookup failure', [{ code: 'missing_location', stop_id: 'a' }], '重新查询地点与路线'],
    ['planning conflict', [{ code: 'city_arrival', stop_id: 'a' }], '让 AI 调整行程'],
  ]) {
    const plan = { ...base, validation: { errors } }
    const html = await renderToString(createSSRApp(Recovery, { plan, busy: false, checking: false }))
    assert(html.includes(expected)); console.log(`${label}: passed`)
  }
  const unlocated = { ...base, itinerary: { days: [{ stops: [{ id: 'a', location_behavior: 'unlocated' }] }] }, validation: { errors: [{ code: 'missing_location', stop_id: 'a' }] } }
  assert.equal(planRecovery(unlocated).lookupCount, 0); assert.equal(planRecovery(unlocated).planningCount, 1)
  const weather = { status: 'unavailable', days: [], failure_reason: '天气服务连接失败，可检查网络后重试天气。' }
  const html = await renderToString(createSSRApp(Weather, { city: '上海', date: '2026-10-21', weather }))
  assert(html.includes(weather.failure_reason)); assert(html.includes('重试天气')); assert(html.includes('不调用大模型'))
  console.log('weather cause and independent retry: passed')

  const t = harness(), d = t.setup('App.vue')
  const original = { ...base, request: { ...d.form }, weather: { status: 'unavailable', requested_city: '上海', days: [] } }
  d.plan.value = original
  let calls = 0, finish
  t.api.api = async (url, init) => {
    calls++; assert(url.endsWith('/weather?city=%E4%B8%8A%E6%B5%B7')); assert.equal(init.method, 'POST')
    return { status: 'available', requested_city: '上海', days: [] }
  }
  await d.retryWeather('上海')
  assert.equal(calls, 1); assert.equal(d.plan.value.weather.status, 'unavailable')
  assert.equal(d.weatherOverrides.value['上海'].status, 'available')
  t.api.api = () => new Promise(resolve => { finish = resolve })
  const pending = d.retryWeather('上海')
  d.plan.value = { ...original, id: 'other-plan' }
  finish({ status: 'available' }); await pending
  assert.equal(Object.keys(d.weatherOverrides.value).length, 0)
  assert.equal(d.weatherRetryCity.value, ''); t.cleanup()
  console.log('weather retry leaves saved snapshot intact and ignores switched-plan response: passed')
} finally { await server.close() }
