// Run Vue setup/watch logic in memory; never launch a browser or call an API.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'

const root = fileURLToPath(new URL('../', import.meta.url)), sourceRoot = path.join(root, 'src')
let groups = 0
async function check(name, fn) { await fn(); groups++; console.log(`PASS ${name}`) }

export function harness(search = '', reduced = false) {
  const hooks = [], cleanups = [], listeners = new Map(), timers = new Map(), dataset = {}, observers = [], requests = []
  let timerId = 0, elapsedTime = 0
  class TestElement {
    bounds = { top: 80, bottom: 480, left: 40, right: 1240, width: 1200, height: 400 }
    getBoundingClientRect() { return this.bounds }
  }
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this) }
    observe(element) { this.element = element }
    disconnect() { this.element = null }
    fire(visible) { this.callback([{ isIntersecting: visible }]) }
  }
  const media = { matches: reduced, addEventListener: (name, fn) => listeners.set('media:' + name, fn), removeEventListener: name => listeners.delete('media:' + name) }
  const widthMedia = { matches: true, addEventListener: (name, fn) => listeners.set('width:' + name, fn), removeEventListener: name => listeners.delete('width:' + name) }
  const document = { documentElement: { dataset }, hidden: false, addEventListener: (name, fn) => listeners.set('doc:' + name, fn), removeEventListener: name => listeners.delete('doc:' + name), getElementById: () => null }
  const window = { location: new URL('http://127.0.0.1:7862/' + search), innerHeight: 900, innerWidth: 1440, matchMedia: query => query.includes('min-width') ? widthMedia : media, addEventListener: (name, fn) => listeners.set('window:' + name, fn), removeEventListener: name => listeners.delete('window:' + name) }
  window.history = { state: { preserved: true }, replaceState(state, _, url) { assert.equal(state.preserved, true); window.location = new URL(url, window.location) } }
  const api = { api: async (...args) => { requests.push(args); return [] }, stream: async (...args) => { requests.push(args) } }
  const cache = new Map()
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename)
    let source = readFileSync(filename, 'utf8')
    if (filename.endsWith('.vue')) { const { descriptor } = parse(source, { filename }); source = compileScript(descriptor, { id: 'motion-check' }).content }
    const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
    const context = {
      exports: {}, console, Error, AbortController, DOMException, URL, URLSearchParams, HTMLElement: TestElement,
      IntersectionObserver: Observer, document, window, performance: { now: () => elapsedTime },
      sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
      setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay, due: elapsedTime + delay }); return timerId }, clearTimeout: id => timers.delete(id),
      setInterval: (fn, delay) => { timers.set(++timerId, { fn, delay, due: elapsedTime + delay }); return timerId }, clearInterval: id => timers.delete(id),
      require(name) {
        if (name === 'vue') return { ...vue, onMounted: fn => hooks.push(fn), onBeforeUnmount: fn => cleanups.push(fn) }
        if (name === 'lucide-vue-next' || name.endsWith('.vue')) return new Proxy({}, { get: () => ({}) })
        if (name.endsWith('/api')) return api
        return load(path.resolve(path.dirname(filename), name + (path.extname(name) ? '' : '.ts')))
      },
    }
    vm.runInNewContext(output, context, { filename }); cache.set(filename, context.exports); return context.exports
  }
  const scope = vue.effectScope()
  return { load, scope, dataset, observers, TestElement, media, widthMedia, window, document, listeners, timers, requests, api,
    advance(ms) {
      const target = elapsedTime + ms
      let next
      while ((next = [...timers.entries()].filter(([, timer]) => timer.due <= target).sort((a, b) => a[1].due - b[1].due)[0])) {
        elapsedTime = next[1].due; timers.delete(next[0]); next[1].fn()
      }
      elapsedTime = target
    },
    setup(relative, props = {}) {
      return scope.run(() => load(path.join(sourceRoot, relative)).default.setup(vue.reactive(props), { expose() {}, emit() {} }))
    },
    useMotion() { return scope.run(() => load(path.join(sourceRoot, 'appearance/usePageMotion.ts')).usePageMotion()) },
    mount() { hooks.forEach(fn => fn()) },
    cleanup() { cleanups.forEach(fn => fn()); scope.stop() },
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
await check('original illustration now has motion by default, with no API requests', () => {
  const t = harness(), a = t.useMotion()
  assert.equal(a.motion.value, true); assert.equal(t.dataset.roamTheme, 'city'); assert.equal(t.dataset.roamMotion, 'on')
  assert.equal(t.dataset.roamAmbient, 'paused'); assert.equal(t.requests.length, 0); t.cleanup(); assert.deepEqual(t.dataset, {})
})
await check('motion switch preserves URL/state and supports static direct links and back/forward', () => {
  const t = harness('?other=1#planner'), a = t.useMotion(); t.mount()
  a.toggleMotion(); assert.equal(t.dataset.roamMotion, 'off'); assert.equal(t.window.location.search, '?other=1&motion=off')
  a.toggleMotion(); assert.equal(t.dataset.roamMotion, 'on'); assert.equal(t.window.location.hash, '#planner')
  t.window.location = new URL('http://127.0.0.1:7862/?motion=off'); t.listeners.get('window:popstate')()
  assert.equal(a.motion.value, false); t.cleanup()
  const direct = harness('?motion=off'), staticMotion = direct.useMotion(); assert.equal(staticMotion.motion.value, false); direct.cleanup()
})
await check('system reduced motion wins and reacts to changes', () => {
  const t = harness('', true), a = t.useMotion(); t.mount()
  assert.equal(a.reduced.value, true); assert.equal(t.dataset.roamMotion, 'off')
  t.media.matches = false; t.listeners.get('media:change')(); assert.equal(a.motion.value, true)
  t.media.matches = true; t.listeners.get('media:change')(); assert.equal(a.motion.value, false); t.cleanup()
})
await check('scenery pauses offscreen/background; entrance runs once and cleans up', async () => {
  const t = harness(), a = t.useMotion(); t.mount()
  a.setHero(new t.TestElement()); await vue.nextTick(); t.observers.at(-1).fire(true); await vue.nextTick()
  assert.equal(t.dataset.roamAmbient, 'running'); assert.equal(a.introPlaying.value, true)
  Array.from(t.timers.values()).forEach(timer => timer.fn()); assert.equal(a.introPlaying.value, false)
  t.document.hidden = true; t.listeners.get('doc:visibilitychange')(); assert.equal(t.dataset.roamAmbient, 'paused')
  t.document.hidden = false; t.listeners.get('doc:visibilitychange')(); assert.equal(t.dataset.roamAmbient, 'running')
  t.observers.at(-1).fire(false); assert.equal(t.dataset.roamAmbient, 'paused')
  t.observers.at(-1).fire(true); await vue.nextTick(); assert.equal(a.introPlaying.value, false)
  a.setHero(null); await vue.nextTick(); assert.equal(t.dataset.roamAmbient, 'paused')
  t.cleanup(); assert.equal(t.listeners.size, 0); assert.deepEqual(t.dataset, {}); assert.equal(t.observers.at(-1).element, null)
})
await check('visible scenery starts before observer delivery and remeasures on return', async () => {
  const t = harness(), a = t.useMotion(); t.mount()
  const hero = new t.TestElement()
  a.setHero(hero); await vue.nextTick(); assert.equal(t.dataset.roamAmbient, 'running')
  t.document.hidden = true; t.listeners.get('doc:visibilitychange')(); assert.equal(t.dataset.roamAmbient, 'paused')
  hero.bounds.top = -500; hero.bounds.bottom = -100
  t.document.hidden = false; t.listeners.get('doc:visibilitychange')(); assert.equal(t.dataset.roamAmbient, 'paused')
  hero.bounds.top = 80; hero.bounds.bottom = 480
  t.listeners.get('doc:visibilitychange')(); assert.equal(t.dataset.roamAmbient, 'running'); t.cleanup()
})
await check('disabling motion cancels date fade and prevents new fades', () => {
  const t = harness(), a = t.useMotion(), module = t.load(path.join(sourceRoot, 'appearance/usePageMotion.ts'))
  let count = 0, cancelled = 0
  const element = { animate() { count++; return { cancel() { cancelled++ }, finished: new Promise(() => {}) } } }
  module.timelineEnter.updated(element, { value: 1, oldValue: 0 }); assert.equal(count, 1)
  module.timelineEnter.updated(element, { value: 1, oldValue: 1 }); assert.equal(count, 1)
  a.toggleMotion(); assert.equal(cancelled, 1)
  module.timelineEnter.updated(element, { value: 2, oldValue: 1 }); assert.equal(count, 1); t.cleanup()
})
await check('motion toggling preserves current form, plan, day and selected stop', async () => {
  const t = harness(), d = t.setup('App.vue')
  const plan = vue.reactive({ id: 'stored-plan', itinerary: { days: [{ stops: [] }, { stops: [] }] }, validation: { total_cents: 838700, budget_cents: 500000 } })
  d.plan.value = plan; d.form.origin = '长沙'; d.form.people = 4; d.activeDay.value = 1; d.selectedStopId.value = 'same-stop'
  for (let i = 0; i < 4; i++) {
    d.toggleMotion(); await vue.nextTick()
    assert.equal(d.plan.value, plan); assert.equal(d.form.origin, '长沙'); assert.equal(d.form.people, 4)
    assert.equal(d.activeDay.value, 1); assert.equal(d.selectedStopId.value, 'same-stop'); assert.equal(d.plan.value.validation.total_cents, 838700)
  }
  assert.equal(t.requests.length, 0); t.cleanup()
})
await check('legacy background links keep app state and unrelated URL parameters when motion changes', async () => {
  for (const search of ['', '?background=unknown&other=1#planner', '?background=journey&other=1#planner']) {
    const t = harness(search), d = t.setup('App.vue')
    const plan = vue.reactive({ id: 'existing-version', itinerary: { days: [{ stops: [] }] } })
    d.plan.value = plan; d.form.origin = '长沙'; d.selectedStopId.value = 'selected-poi'
    d.toggleMotion(); await vue.nextTick()
    assert.equal(d.plan.value, plan); assert.equal(d.form.origin, '长沙'); assert.equal(d.selectedStopId.value, 'selected-poi')
    assert.equal(new URLSearchParams(t.window.location.search).get('motion'), 'off')
    assert.equal(new URLSearchParams(t.window.location.search).get('background'), new URLSearchParams(search.split('#')[0]).get('background'))
    assert.equal(t.window.location.hash, search.includes('#planner') ? '#planner' : '')
    assert.equal(t.requests.length, 0); t.cleanup()
  }
})
await check('static background leaves no timer or network request and cleans listeners', () => {
  const b = harness(), backdrop = b.setup('appearance/JourneyBackdrop.vue', { motion: false }); b.mount()
  assert.equal(backdrop.pageVisible.value, true)
  b.document.hidden = true; b.listeners.get('doc:visibilitychange')(); assert.equal(backdrop.pageVisible.value, false)
  b.document.hidden = false; b.listeners.get('doc:visibilitychange')(); assert.equal(backdrop.pageVisible.value, true)
  assert.equal(b.requests.length, 0); assert.equal(b.timers.size, 0); b.cleanup(); assert.equal(b.listeners.size, 0)
})
await check('single airliner runs for 24 seconds every 50-second cycle with no bird traffic', () => {
  const t = harness(), { skyEvents, skyCycleMs } = t.load(path.join(sourceRoot, 'appearance/sceneTraffic.ts'))
  const birds = skyEvents.filter(event => event.kind === 'birds'), planes = skyEvents.filter(event => event.kind === 'plane')
  assert.equal(birds.length, 0); assert.equal(planes.length, 1)
  assert.equal(skyCycleMs, 50000); assert.equal(planes[0].start, 8000)
  for (const plane of planes) { assert.equal(plane.end - plane.start, 24000); assert(plane.end < skyCycleMs) }
  for (let i = 1; i < skyEvents.length; i++) assert(skyEvents[i].start >= skyEvents[i - 1].end)
  t.cleanup()
})
await check('scene clock pauses in background, resumes exact flight position and cleans timers', () => {
  const t = harness(), props = vue.reactive({ motion: true })
  const scene = t.setup('appearance/JourneyBackdrop.vue', props); t.mount()
  assert.equal(scene.traffic.value, null); assert.equal(t.timers.size, 1)
  t.advance(10000); assert.equal(scene.traffic.value, 'plane')
  t.document.hidden = true; t.listeners.get('doc:visibilitychange')(); assert.equal(t.timers.size, 0)
  t.advance(100000)
  t.document.hidden = false; t.listeners.get('doc:visibilitychange')()
  assert.equal(scene.traffic.value, 'plane'); assert.equal(scene.flightProgressMs.value, 2000)
  t.advance(22000); assert.equal(scene.traffic.value, null)
  t.advance(26000); assert.equal(scene.traffic.value, 'plane')
  t.advance(24000); assert.equal(scene.traffic.value, null)
  props.motion = false; assert.equal(t.timers.size, 0)
  t.cleanup(); assert.equal(t.timers.size, 0); assert.equal(t.listeners.size, 0); assert.equal(t.requests.length, 0)
})
await check('scene clock stays active without hero and stops on tablet or motion-off', () => {
  const t = harness(), a = t.useMotion(), props = vue.reactive({ motion: true })
  const scene = t.setup('appearance/JourneyBackdrop.vue', props); t.mount()
  assert.equal(t.dataset.roamAmbient, 'paused'); assert.equal(scene.traffic.value, null); assert.equal(t.timers.size, 1)
  t.widthMedia.matches = false; t.listeners.get('width:change')(); assert.equal(t.timers.size, 0)
  t.advance(60000); t.widthMedia.matches = true; t.listeners.get('width:change')(); assert.equal(t.timers.size, 1)
  assert.equal(scene.flightProgressMs.value, 0)
  props.motion = false; assert.equal(t.timers.size, 0)
  a.toggleMotion(); assert.equal(t.dataset.roamMotion, 'off'); t.cleanup(); assert.equal(t.timers.size, 0)
})
await check('source, QA and map components remain mounted', () => {
  const app = readFileSync(path.join(sourceRoot, 'App.vue'), 'utf8')
  assert.equal((app.match(/<StudyQA\b/g) || []).length, 1); assert.equal((app.match(/<RouteMap\b/g) || []).length, 1)
  assert.doesNotMatch(app, /AppearanceToolbar|AppearanceScene|selectAppearance|:key="[^\"]*(?:motion|appearance)/)
  const source = readFileSync(path.join(sourceRoot, 'components/SourceDrawer.vue'), 'utf8')
  assert.match(source, /sourceParts\(fact.text, terms\)/); assert.match(source, /trapFocus/); assert.match(source, /trigger.value\?\.focus/)
})
await check('modified templates compile, and only individual SVG scenery animates', () => {
  for (const item of ['App.vue', 'components/SourceDrawer.vue', 'components/CityScene.vue', 'appearance/JourneyBackdrop.vue', 'appearance/JourneyCity.vue']) {
    const filename = path.join(sourceRoot, item), { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
    const script = descriptor.scriptSetup ? compileScript(descriptor, { id: item }) : null
    const result = compileTemplate({ source: descriptor.template.content, filename, id: item, compilerOptions: { bindingMetadata: script?.bindings } })
    assert.deepEqual(result.errors, [])
  }
  const css = readFileSync(path.join(sourceRoot, 'appearance/appearance.css'), 'utf8')
  assert.match(css, /scene-ambient[\s\S]*animation-play-state: paused/)
  assert.doesNotMatch(css, /\.city-scene\s*>\s*svg\s*\{[^}]*animation:/)
})
console.log(`${groups} motion regression groups passed; no browser, API, model or database calls.`)

}
