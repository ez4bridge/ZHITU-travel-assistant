import assert from 'node:assert/strict'
import { build } from 'esbuild'

// Bundle exactly the browser adapter so Node can exercise its real request boundary.
const compiled = await build({ entryPoints: [new URL('../src/conversationApi.ts', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
  bundle: true, write: false, format: 'esm', platform: 'node' })
const { conversationPreview } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const originalFetch = globalThis.fetch
const command = { base_fingerprint: 'a'.repeat(64), activities: [{ stop_id: 'A', status: 'cancelled', retain_cost: true }], day_indexes: [0] }
try {
  globalThis.fetch = async (path, init) => {
    assert.equal(path, '/api/plans/id%2Fone/conversation-preview')
    const payload = JSON.parse(init.body)
    assert.deepEqual(payload.command, command, 'structured cancellation and fee protection must survive the conversation boundary')
    assert.equal(payload.instruction, '改去故宫')
    assert.equal(payload.messages.length, 10)
    assert.equal(payload.messages[0].content, '2')
    assert.equal(payload.messages[9].content.length, 2000)
    assert.equal(init.signal instanceof AbortSignal, true)
    return new Response(JSON.stringify({ assistant_message: '请确认游玩时长', unresolved: ['需要游玩几小时？'], proposal_summary: [], preview: null }), { status: 200 })
  }
  const messages = Array.from({ length: 12 }, (_, index) => ({ role: index % 2 ? 'assistant' : 'user', content: index === 11 ? 'x'.repeat(3000) : String(index) }))
  const result = await conversationPreview('id/one', command, '改去故宫', messages, new AbortController().signal)
  assert.equal(result.preview, null)
  assert.deepEqual(result.unresolved, ['需要游玩几小时？'])
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: '旧版本不能覆盖已确认进度' }), { status: 409 })
  await assert.rejects(() => conversationPreview('id/one', command, '', [], new AbortController().signal),
    error => error.status === 409 && error.message === '旧版本不能覆盖已确认进度')
  console.log('conversationApi: combined choices, bounded conversation, clarification and stale protection passed')
} finally { globalThis.fetch = originalFetch }
