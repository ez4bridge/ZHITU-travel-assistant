import type { CityTransfer, TripRequest } from './types'

export type TransportRequest = Pick<TripRequest, 'origin' | 'destination' | 'destinations' | 'include_origin_transport' | 'return_to_origin' | 'intercity_mode' | 'intercity_modes'>
const cityKey = (city: string) => city.trim().replace(/市$/, '')
const pairKey = (from: string, to: string) => JSON.stringify([cityKey(from), cityKey(to)])

export function journeyCities(request: TransportRequest): string[] {
  const cities = request.destinations?.length ? request.destinations.map(d => d.city.trim())
    : request.destination.split(/\s*(?:→|->|、|，|,|；|;|\n)\s*/).filter(Boolean).map(c => c.trim())
  if ((request.include_origin_transport || request.return_to_origin) && request.origin.trim()) cities.unshift(request.origin.trim())
  if (request.return_to_origin && request.origin.trim()) cities.push(request.origin.trim())
  return cities.filter((city, i) => city && (!i || cityKey(city) !== cityKey(cities[i - 1]!)))
}

export function transferCoverage(request: TransportRequest, transfers: CityTransfer[]) {
  const cities = journeyCities(request)
  const expected = cities.slice(1).map((to, i) => ({ from: cities[i]!, to }))
  const actual = transfers.map(t => pairKey(t.from_city, t.to_city))
  const remaining = [...actual]
  const missing = expected.filter(leg => {
    const index = remaining.indexOf(pairKey(leg.from, leg.to))
    if (index < 0) return true
    remaining.splice(index, 1); return false
  })
  return { expected, missing, complete: JSON.stringify(expected.map(t => pairKey(t.from, t.to))) === JSON.stringify(actual) }
}

export function transportRequestChanged(draft: TransportRequest, saved: TransportRequest): boolean {
  const signature = (r: TransportRequest) => JSON.stringify({
    cities: journeyCities(r).map(cityKey), outbound: Boolean(r.origin.trim() && (r.include_origin_transport || r.return_to_origin)),
    inbound: Boolean(r.origin.trim() && r.return_to_origin), modes: journeyCities(r).length > 1 ? [...(r.intercity_modes?.length ? r.intercity_modes : [r.intercity_mode])].sort() : null,
  })
  return signature(draft) !== signature(saved)
}

export function transportScopeText(request: TransportRequest): string {
  const parts = [request.include_origin_transport || request.return_to_origin ? '含出发交通' : '不含出发交通',
    request.return_to_origin ? '含返程交通' : '不含返程交通']
  return `${journeyCities(request).join(' → ')}；${parts.join('，')}。`
}
