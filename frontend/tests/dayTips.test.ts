import assert from 'node:assert/strict'
import { groupTravelTips } from '../src/dayTips.ts'
import type { Plan } from '../src/types.ts'

const plan = { itinerary: {
  days: [
    { date: '2026-10-21', tips: ['当天已明确的提醒'], stops: [{ name: '北京环球度假区', city: '北京市' }] },
    { date: '2026-10-22', stops: [{ name: '延吉西市场', city: '延吉市' }] },
    { date: '2026-10-23', stops: [{ name: '哈尔滨冰雪大世界', city: '哈尔滨市' }] },
  ], tips: ['当天已明确的提醒', '第2天注意用餐时间', '10月23日活动请确认开放', '北京环球度假区需要预约',
    '哈尔滨天气请另行核实', '各城温差可能较大，注意衣物', '第9天的旧日期不属于本行程', '北京市与哈尔滨市温差请核实', '预算4999.98元为2人全程上限，北京到延吉可另查铁路'],
} } as unknown as Plan
const result = groupTravelTips(plan)
assert.deepEqual(result.days[0], ['当天已明确的提醒', '北京环球度假区需要预约'])
assert.deepEqual(result.days[1], ['第2天注意用餐时间'])
assert.deepEqual(result.days[2], ['10月23日活动请确认开放', '哈尔滨天气请另行核实'])
assert.deepEqual(result.general, ['各城温差可能较大，注意衣物', '第9天的旧日期不属于本行程', '北京市与哈尔滨市温差请核实', '预算4999.98元为2人全程上限，北京到延吉可另查铁路'])
console.log('dayTips: day/date/place/city attribution and uncertain reminders passed')
plan.itinerary.tips = ['哈尔滨冰雪大世界10月22日可能未开放，请核实']
const conflictingDate = groupTravelTips(plan)
assert(conflictingDate.days[1]!.includes(plan.itinerary.tips[0]!))
assert(conflictingDate.days[2]!.includes(plan.itinerary.tips[0]!))
plan.itinerary.tips = ['延吉机场到市区、哈尔滨机场到市区均需留时间']
assert.deepEqual(groupTravelTips(plan).general, plan.itinerary.tips)
