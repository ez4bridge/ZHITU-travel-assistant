import type { Stop } from './types'

type PlaceLabels = Pick<Stop, 'name' | 'matched_name' | 'address'>
export interface SourcePart { text: string; highlighted: boolean }

function foldedText(text: string) {
  let value = '', offset = 0
  const positions: { start: number; end: number }[] = []
  for (const character of text) {
    const folded = character.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
    value += folded
    for (let i = 0; i < folded.length; i++) positions.push({ start: offset, end: offset + character.length })
    offset += character.length
  }
  return { value, positions }
}

export function placeTerms(place?: PlaceLabels): string[] {
  if (!place) return []
  const names = [place.name, place.matched_name].filter(Boolean)
  const candidates = names.flatMap(name => [name, name.replace(/(?:纪念馆|博物馆)$/, ''),
    ...[...name.matchAll(/[（(]([^（）()]+)[）)]/g)].map(match => match[1]!)])
  // A street-office prefix is metadata, not part of the numbered street address.
  const address = place.address.replace(/[（(].*$/, '').replace(/^.*(?:街道办事处|街道)/, '').trim()
  candidates.push(address)
  return [...new Set(candidates.map(candidate => foldedText(candidate).value).filter(term => term.length >= 4))]
    .sort((a, b) => b.length - a.length)
}

export function sourceParts(text: string, terms: string[]): SourcePart[] {
  const folded = foldedText(text)
  const ranges: { start: number; end: number }[] = []
  for (const term of terms) {
    if (term.length < 4) continue
    let from = 0
    while (from < folded.value.length && ranges.length < 80) {
      const at = folded.value.indexOf(term, from)
      if (at < 0) break
      ranges.push({ start: folded.positions[at]!.start, end: folded.positions[at + term.length - 1]!.end })
      from = at + term.length
    }
  }
  const merged: { start: number; end: number }[] = []
  for (const range of ranges.sort((a, b) => a.start - b.start || b.end - a.end)) {
    const previous = merged.at(-1)
    if (previous && range.start <= previous.end) previous.end = Math.max(previous.end, range.end)
    else merged.push({ ...range })
  }
  const parts: SourcePart[] = []
  let cursor = 0
  for (const range of merged) {
    if (range.start > cursor) parts.push({ text: text.slice(cursor, range.start), highlighted: false })
    parts.push({ text: text.slice(range.start, range.end), highlighted: true })
    cursor = range.end
  }
  if (cursor < text.length || !parts.length) parts.push({ text: text.slice(cursor), highlighted: false })
  return parts
}
