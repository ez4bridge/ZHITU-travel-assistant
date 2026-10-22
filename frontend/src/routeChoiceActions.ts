import type { Mode, RouteLeg } from './types'

export function routeChoiceAction(route: Pick<RouteLeg, 'scope' | 'stationary' | 'mode'>,
  mode: Mode, available: boolean, queryable: boolean, disabled: boolean) {
  const local = route.scope === 'local' && !route.stationary
  const selected = route.mode === mode && available
  return {
    enabled: local && !disabled && !selected && (available || queryable),
    requiresQuery: !available,
    label: available ? selected ? '已选' : '在调整中预览' : '查询并预览',
  }
}
