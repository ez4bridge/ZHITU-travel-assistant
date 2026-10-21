import assert from 'node:assert/strict'
import { reactive, nextTick } from 'vue'
import { harness } from './check-appearance.mjs'

const t = harness(), instances = [], calls = []
class FakeMap {
  constructor() { instances.push(this) }
  on() {}
  add(items) { this.items = items }
  remove() {}
  setZoomAndCenter(zoom, center) { this.center = center; this.zoom = zoom }
  setFitView() { this.fitted = true }
  destroy() {}
}
class Marker {
  constructor(options) { this.options = options }
  on() {}
}
t.window.AMap = { Map: FakeMap, Marker }
t.document.createElement = () => ({ className: '', textContent: '' })
const centers = { 万州: [108.4, 30.8], 广州: [113.26, 23.12] }
const region = city => ({ status: 'available', approximate: true, city: city + '区', center: centers[city], level: 'district' })
let pending
t.api.api = async url => {
  if (url === '/api/map-config') return { key: 'test-public-key', security_proxy: '/_AMapService' }
  const city = new URL(url, 'http://test').searchParams.get('city'); calls.push(city)
  if (city === '杭州') return new Promise(resolve => { pending = resolve })
  return region(city)
}
const props = reactive({ cities: ['万州'], stops: [{ id: 'a', city: '万州', location: null, location_status: 'unknown' }], routes: [], selectedId: 'a' })
const d = t.setup('components/RouteMap.vue', props)
d.host.value = new t.TestElement(); t.mount()
const settle = async () => { await nextTick(); await new Promise(setImmediate); await nextTick() }
await settle()
assert.deepEqual(Array.from(instances[0].center), centers.万州)
assert.equal(d.regions.value[0].approximate, true)
assert.equal(props.stops[0].location, null)
assert.equal(instances[0].items[0].options.content.textContent, '万州区 · 区域位置')
console.log('region overview does not modify stop coordinates or routes: passed')

props.stops[0] = { ...props.stops[0], location: [108.3, 30.7], location_status: 'verified' }
await settle()
assert.equal(d.hasPoints.value, true); assert.equal(d.regions.value.length, 0)
assert.equal(calls.length, 1); assert.equal(instances[0].fitted, true)
console.log('verified POIs retain precise map and skip region requests: passed')

props.stops[0] = { ...props.stops[0], location: null, location_status: 'unknown' }
await settle(); assert.equal(calls.length, 1)
props.cities = ['杭州']; await settle()
props.cities = ['广州']; await settle()
pending({ ...region('万州'), city: '迟到的旧城市' }); await settle()
assert.equal(d.regions.value[0].city, '广州区')
assert.deepEqual(Array.from(instances[0].center), centers.广州)
t.cleanup()
console.log('cached regions and city-switch cancellation prevent stale map centers: passed')
