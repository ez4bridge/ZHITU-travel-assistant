// Offline regression checks for locating a place without trimming or rewriting its evidence.
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
  server: { middlewareMode: true }, appType: 'custom',
  optimizeDeps: { noDiscovery: true, include: [] },
})
try {
  const { placeTerms, sourceParts } = await server.ssrLoadModule('/src/sourceHighlights.ts')
  const place = { name: '城市文化发起地（《青年周刊》编辑部）旧址', matched_name: '', address: '东城街道春风路100弄2号（地铁站附近）' }
  const terms = placeTerms(place)
  const highlighted = parts => parts.filter(part => part.highlighted).map(part => part.text)
  const preserved = (text, parts) => assert.equal(parts.map(part => part.text).join(''), text)

  const before = '这里介绍另一个场馆的建筑和会议。'.repeat(20)
  const text = before + '3、城市文化成立地（《青年周刊》编辑部）旧址\n位于春风路100弄2号。这里才是当前地点相关的原文。'
  const parts = sourceParts(text, terms)
  preserved(text, parts)
  assert.ok(highlighted(parts).some(value => value.includes('青年周刊')))
  assert.ok(!highlighted(parts).some(value => value.includes('另一个场馆')))
  assert.ok(parts[0].text.length > 180, 'locate beyond the old preview cutoff')
  console.log('late paragraph and parenthesized alias: passed')

  const spaced = '📍原文地址：春风路１００弄 ２号。文字与标点均保留。'
  const addressParts = sourceParts(spaced, terms)
  preserved(spaced, addressParts)
  assert.deepEqual(highlighted(addressParts), ['春风路１００弄 ２号'])
  console.log('full-width digits, whitespace and emoji offsets: passed')

  const unrelated = '另一篇原文介绍不相关的地点，内容应全部保留。'
  const untouched = sourceParts(unrelated, terms)
  assert.deepEqual(untouched, [{ text: unrelated, highlighted: false }])
  assert.deepEqual(placeTerms(), [])
  assert.deepEqual(sourceParts('', terms), [{ text: '', highlighted: false }])
  console.log('no match, no place and empty evidence: passed')

  const overlap = sourceParts('城市文化纪念馆位于古镇路100号。', placeTerms({ name: '城市文化纪念馆', matched_name: '城市文化', address: '古镇路100号' }))
  preserved('城市文化纪念馆位于古镇路100号。', overlap)
  assert.deepEqual(highlighted(overlap), ['城市文化纪念馆', '古镇路100号'])
  assert.ok(!placeTerms({ name: '纪念馆', matched_name: '', address: '' }).length)
  console.log('overlapping names and short generic labels: passed')

  const bookOnly = sourceParts('这段只提《科学与教育》，没有说明具体场馆。', placeTerms({ name: '《科学与教育》展示馆', matched_name: '', address: '' }))
  assert.equal(highlighted(bookOnly).length, 0, 'a book title alone is not a place alias')
  const literal = '<img src=x onerror=alert(1)>城市文化纪念馆<script>坏内容</script>'
  preserved(literal, sourceParts(literal, ['城市文化纪念馆']))
  console.log('book-title false positives and unchanged literal markup: passed')
} finally { await server.close() }
