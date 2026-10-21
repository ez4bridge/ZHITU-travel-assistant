// Pure display fixtures: no server, search service or model calls.
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
  const { default: Card } = await server.ssrLoadModule('/src/components/VisitAdvisory.vue')
  const base = { status: 'documented_open', note: '来自本地保存的参观公告', windows: [], last_entry: null, source_ids: [], free_admission: null }
  const props = { policy: base, date: '2026-10-26', sources: [{ id: 'notice', title: '场馆公告', url: 'https://example.gov.cn/notice' }], stop: { start: '09:00', end: '10:00' } }
  const cases = [
    ['Monday closure', { ...base, status: 'documented_closed' }, '该日闭馆'],
    ['not visitable', { ...base, status: 'not_visitable' }, '不对外开放'],
  ]
  for (const [label, policy, expected] of cases) {
    const html = await renderToString(createSSRApp(Card, { ...props, policy }))
    assert.ok(html.includes(expected), label)
    assert.ok(html.includes('不产生联网或模型调用'), label)
    assert.ok(html.includes('2026-10-26'), label)
    console.log(`${label}: passed`)
  }
  for (const [label, policy] of [
    ['unknown holiday notice', { ...base, status: 'unknown', note: '未覆盖10月26日，不能据此确认当天开放；该假期公告称免费、无需预约，仅作参考。' }],
    ['open with Monday exception', { ...base, note: '周二至周日开放，周一闭馆。' }],
    ['outside window', { ...base, windows: [['11:00', '12:00']] }],
    ['last entry', { ...base, last_entry: '08:59' }],
  ]) {
    const html = await renderToString(createSSRApp(Card, { ...props, policy }))
    assert.ok(!html.includes('visit-advisory'), label)
    assert.ok(!html.includes('不能据此'), label)
    console.log(`${label}: hidden`)
  }
  const withoutSource = await renderToString(createSSRApp(Card, { ...props, policy: { ...base, status: 'documented_closed' }, sources: [] }))
  assert.ok(!withoutSource.includes('visit-advisory'), 'closure without source hidden')
  console.log('closure without source: hidden')
} finally { await server.close() }
