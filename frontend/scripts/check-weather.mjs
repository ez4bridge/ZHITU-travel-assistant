// Conditional render smoke checks without launching a browser or calling any API.
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
  plugins: [vue()], server: { middlewareMode: true }, appType: 'custom',
  optimizeDeps: { noDiscovery: true, include: [] },
})
try {
  const { default: Card } = await server.ssrLoadModule('/src/components/WeatherCard.vue')
  const { weatherAdvice } = await server.ssrLoadModule('/src/weatherAdvice.ts')
  const day = { date: '2026-10-20', status: 'available', day_weather: '小雨', night_weather: '阴',
    day_temp: 22, night_temp: 18, day_wind: '东', night_wind: '东', day_power: '1-3', night_power: '1-3' }
  const weather = { provider: 'amap', city: '上海市', requested_city: '上海', status: 'available',
    issued_at: '2026-10-19T11:00:00+08:00', fetched_at: '2026-10-19T11:01:00+08:00',
    from_cache: false, days: [day] }
  const cases = [
    ['rain', { weather, date: day.date }, '小雨'],
    ['uncovered date', { weather, date: '2026-11-20' }, '该日期暂无预报'],
    ['API failure', { weather: { ...weather, status: 'unavailable', days: [] }, date: day.date }, '天气暂不可用'],
    ['legacy plan', { weather: null, date: day.date }, '此版本未保存天气信息'],
    ['QWeather source and high/low temperatures', { weather: { ...weather, provider: 'qweather',
      days: [{ ...day, day_temp: null, night_temp: null, temp_max: 26, temp_min: 18 }] }, date: day.date }, '和风天气'],
  ]
  for (const [label, props, expected] of cases) {
    const html = await renderToString(createSSRApp(Card, props))
    assert.ok(html.includes(expected), label)
    assert.ok(!html.includes('NaN'), label)
    if (label === 'rain') {
      for (const text of ['穿衣建议', '带伞或雨衣', '防滑鞋', '室内景点作为备选']) assert.ok(html.includes(text), text)
    }
    if (['uncovered date', 'API failure', 'legacy plan'].includes(label)) assert.ok(!html.includes('当天出行建议'), label)
    if (label.startsWith('QWeather')) {
      assert.ok(html.includes('最高 26') && html.includes('最低 18'), label)
      assert.ok(!html.includes('白天 26'), label)
    }
    console.log(`${label}: passed`)
  }
  const adviceText = snapshot => weatherAdvice(snapshot, day.date).map(item => item.text).join(' ')
  const qweather = { ...weather, provider: 'qweather' }
  const sunny = { ...day, day_weather: '晴', night_weather: '晴', temp_max: 32, temp_min: 26 }
  assert.match(adviceText({ ...qweather, days: [sunny] }), /轻薄透气/)
  assert.match(adviceText({ ...qweather, days: [sunny] }), /补水休息/)
  const snow = { ...sunny, day_weather: '雨夹雪', temp_max: 3, temp_min: -2 }
  assert.match(adviceText({ ...qweather, days: [snow] }), /保暖外套/)
  assert.match(adviceText({ ...qweather, days: [snow] }), /结冰/)
  const missing = { ...sunny, temp_max: null, temp_min: null }
  assert.ok(!weatherAdvice({ ...qweather, days: [missing] }, day.date).some(item => item.kind === 'clothing'))
  const overnight = { ...sunny, night_weather: '雷阵雨', night_power: '6-7' }
  assert.match(adviceText({ ...qweather, days: [overnight] }), /谨慎撑伞/)
  assert.match(adviceText({ ...qweather, days: [overnight] }), /如遇雷电/)
  assert.deepEqual(weatherAdvice({ ...qweather, days: [sunny] }, '2026-10-21'), [])
  assert.deepEqual(weatherAdvice({ ...qweather, status: 'unavailable', days: [sunny] }, day.date), [])
  console.log('forecast-linked advice and missing data: passed')
} finally {
  await server.close()
}
