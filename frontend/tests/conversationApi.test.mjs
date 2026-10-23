import assert from 'node:assert/strict'
import { build } from 'esbuild'

// Bundle exactly the browser adapter so Node can exercise its real request boundary.
const compiled = await build({ entryPoints: [new URL('../src/conversationApi.ts', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
  bundle: true, write: false, format: 'esm', platform: 'node' })
const { aiAdjustPreview } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const originalFetch = globalThis.fetch
try {
  globalThis.fetch = async (path, init) => {
    assert.equal(path, '/api/plans/id%2Fone/ai-adjust-preview')
    const payload = JSON.parse(init.body)
    assert.equal(payload.base_fingerprint, 'b'.repeat(64))
    assert.equal(payload.expected_state_revision, 2)
    assert.equal(payload.expected_routing_revision, 3)
    assert.equal(payload.day_index, 4)
    assert.equal(payload.command, undefined)
    assert.equal(payload.route_choices[0].mode, 'taxi')
    assert.equal(init.signal instanceof AbortSignal, true)
    return new Response(JSON.stringify({ assistant_message: '已调整', unresolved: [], proposal_summary: [], preview: null }))
  }
  await aiAdjustPreview('id/one', { base_fingerprint: 'b'.repeat(64), state_revision: 2, routing_revision: 3 },
    '改成打车', [], 4, [{ day_index: 4, from_id: 'A', to_id: 'B', mode: 'taxi' }], new AbortController().signal)
  console.log('AI-only preview carries version, scope, selected transport and cancellation without solver commands: passed')
} finally { globalThis.fetch = originalFetch }
