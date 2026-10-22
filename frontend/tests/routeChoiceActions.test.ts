import assert from 'node:assert/strict'
import { routeChoiceAction } from '../src/routeChoiceActions.ts'

const local = { scope: 'local', mode: 'walking', stationary: false } as const
assert.equal(routeChoiceAction(local, 'transit', false, false, false).enabled, false, 'missing endpoint proof must not enable a query')
const query = routeChoiceAction(local, 'transit', false, true, false)
assert.deepEqual(query, { enabled: true, requiresQuery: true, label: '查询并预览' })
assert.equal(routeChoiceAction({ ...local, scope: 'intercity' }, 'transit', false, true, false).enabled, false)
assert.equal(routeChoiceAction({ ...local, stationary: true }, 'taxi', false, true, false).enabled, false)
assert.equal(routeChoiceAction(local, 'taxi', false, true, true).enabled, false, 'an active operation blocks another lookup')
assert.equal(routeChoiceAction(local, 'walking', true, true, false).enabled, false)
assert.equal(routeChoiceAction(local, 'walking', false, true, false).enabled, true, 'a failed current mode can be queried again, never treated as already usable')
assert.equal(routeChoiceAction(local, 'taxi', true, false, false).enabled, true, 'an existing verified candidate remains selectable')
console.log('routeChoiceActions: local verified query gate, protected no-op and retry labels passed')
