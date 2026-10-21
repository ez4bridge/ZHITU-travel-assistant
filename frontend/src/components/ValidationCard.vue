<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, CircleAlert, ChevronDown, RefreshCw, Sparkles } from 'lucide-vue-next'
import type { DayPlan, ValidationReport } from '../types'
import { validationGroups } from '../validationGroups'

const props = defineProps<{ report: ValidationReport; days: DayPlan[]; busy: boolean; checking: boolean }>()
defineEmits<{ revalidate: []; revise: [] }>()
const groups = computed(() => validationGroups(props.report, props.days))
const hasIssues = computed(() => props.report.errors.length + props.report.warnings.length > 0)
const categories = [
  { kind: 'errors', label: '阻断问题' },
  { kind: 'warnings', label: '优化建议' },
  { kind: 'unknowns', label: '待确认信息' },
] as const
</script>

<template>
  <section class="validation-card card" aria-label="出行检查">
    <div class="mini-heading">
      <CircleCheck :size="18" /><h3>出发前，检查一下</h3>
      <button class="icon-button" :disabled="checking || busy" aria-label="重新校验方案" @click="$emit('revalidate')"><RefreshCw :size="15" :class="{ spin: checking }" /></button>
    </div>
    <div class="check-overview" :class="{ blocked: report.errors.length, suggested: !report.errors.length && report.warnings.length }" role="status">
      <component :is="hasIssues ? CircleAlert : CircleCheck" :size="21" />
      <div><strong>{{ report.errors.length ? '行程尚不可执行，请先处理阻断问题' : report.warnings.length ? '部分安排可以进一步优化' : '已知的时间与预算检查通过' }}</strong>
        <p><template v-if="hasIssues || report.unknowns.length">
          <span v-if="report.errors.length">{{ report.errors.length }} 项阻断</span>
          <span v-if="report.warnings.length">{{ report.warnings.length }} 项建议</span>
          <span v-if="report.unknowns.length">{{ report.unknowns.length }} 项待确认</span>
        </template><template v-else>费用、开放与预约仍请出发前核对</template></p>
      </div>
    </div>
    <div v-if="groups.length" class="check-groups">
      <p class="check-help">按天汇总 · 点击展开详情</p>
      <details v-for="group in groups" :key="group.key" class="check-group">
        <summary>
          <div class="check-day"><strong>{{ group.title }}</strong><small v-if="group.date || group.cities">{{ [group.date, group.cities].filter(Boolean).join(' · ') }}</small></div>
          <div class="check-counts">
            <span v-if="group.counts.errors" class="count-errors">{{ group.counts.errors }} 阻断</span>
            <span v-if="group.counts.warnings" class="count-warnings">{{ group.counts.warnings }} 建议</span>
            <span v-if="group.counts.unknowns" class="count-unknowns">{{ group.counts.unknowns }} 待确认</span>
          </div>
          <ChevronDown :size="16" class="check-chevron" />
        </summary>
        <div class="check-body">
          <template v-for="category in categories" :key="category.kind">
            <section v-if="group.rows[category.kind].length" class="check-category" :class="category.kind">
              <h4>{{ category.label }}<span>{{ group.counts[category.kind] }} 项</span></h4>
              <ul><li v-for="row in group.rows[category.kind]" :key="row.key">
                <p>{{ row.message }} <span v-if="row.count > 1" class="check-repeat">共 {{ row.count }} 项</span></p>
                <small v-if="row.places.length">相关地点：{{ row.places.join('、') }}</small>
              </li></ul>
            </section>
          </template>
        </div>
      </details>
    </div>
    <button v-if="hasIssues" class="secondary-button check-revise" :disabled="busy || checking" @click="$emit('revise')"><Sparkles :size="15" />请 AI 调整这些问题</button>
  </section>
</template>

<style scoped>
.validation-card { min-width: 0; }
.check-overview { display: flex; align-items: flex-start; gap: 10px; padding: 13px 14px; margin: 17px 0 0; border-radius: 10px; color: #42745a; background: #f1f6ef; }
.check-overview > svg { flex-shrink: 0; margin-top: 2px; }
.check-overview strong { font-size: 13px; font-weight: 600; line-height: 1.8; }
.check-overview p { display: flex; flex-wrap: wrap; gap: 4px 13px; margin-top: 3px; font-size: 12px; line-height: 1.7; color: #71846f; }
.check-overview.blocked { color: #a66c37; background: #fcf5e9; }
.check-overview.suggested { color: #967744; background: #faf7ef; }
.check-help { font-size: 11px; color: #829180; margin: 15px 0 8px; }
.check-group { border-bottom: 1px solid #e8ede4; }
.check-group summary { display: grid; grid-template-columns: minmax(0, 1fr) auto 16px; align-items: center; gap: 8px 12px; padding: 14px 0; cursor: pointer; list-style: none; border-radius: 5px; }
.check-group summary::-webkit-details-marker { display: none; }
.check-group summary:focus-visible { outline: 2px solid #6e9c7b; outline-offset: 3px; }
.check-day { min-width: 0; }
.check-day strong { display: block; font-size: 13px; color: #45664e; font-weight: 600; }
.check-day small { display: block; margin-top: 3px; font-size: 11px; color: #83917d; line-height: 1.7; overflow-wrap: anywhere; }
.check-counts { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 8px; max-width: 190px; font-size: 11px; line-height: 1.7; }
.check-counts span { white-space: nowrap; }
.count-errors { color: #a2673d; }
.count-warnings { color: #967b45; }
.count-unknowns { color: #7b8a76; }
.check-chevron { color: #8b9d85; transition: transform .15s; }
.check-group[open] .check-chevron { transform: rotate(180deg); }
.check-body { padding: 0 0 15px; }
.check-category + .check-category { margin-top: 16px; }
.check-category h4 { display: flex; align-items: baseline; gap: 8px; font-size: 12px; font-weight: 600; color: #657c60; margin: 0 0 7px; }
.check-category.errors h4 { color: #a2673d; }
.check-category.warnings h4 { color: #967b45; }
.check-category h4 span { font-weight: 400; font-size: 11px; color: #8d9888; }
.check-category ul { list-style: none; padding: 0 0 0 11px; margin: 0; border-left: 2px solid #e7ece2; }
.check-category.errors ul { border-color: #e6c6a9; }
.check-category.warnings ul { border-color: #e8dbc0; }
.check-category li + li { margin-top: 11px; }
.check-category li p { font-size: 12px; color: #6d7b63; line-height: 1.85; margin: 0; overflow-wrap: anywhere; }
.check-category li small { display: block; color: #8a9681; font-size: 11px; line-height: 1.8; margin-top: 3px; overflow-wrap: anywhere; }
.check-repeat { color: #8b967f; font-size: 11px; white-space: nowrap; }
.check-revise { margin-top: 16px; }
@media(max-width:520px) {
  .check-group summary { grid-template-columns: minmax(0, 1fr) 16px; gap: 7px 10px; }
  .check-day { grid-column: 1; }
  .check-counts { grid-column: 1; justify-content: flex-start; max-width: none; }
  .check-chevron { grid-column: 2; grid-row: 1 / 3; }
}
@media(prefers-reduced-motion:reduce) { .check-chevron { transition: none; } }
</style>
