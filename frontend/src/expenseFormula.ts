import type { Expense } from './types'

export function expenseFormula(cost: Pick<Expense, 'unit_cost' | 'quantity' | 'basis'>, people: number): string {
  const units = `¥${cost.unit_cost} × ${cost.quantity}份`
  return cost.basis === 'person' ? `${units} × ${people}人` : `${units}（全员）`
}
