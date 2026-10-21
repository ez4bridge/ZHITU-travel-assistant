import type { WeatherDay, WeatherSnapshot } from './types'

export interface WeatherAdvice {
  kind: 'clothing' | 'gear' | 'activity' | 'wind' | 'visibility'
  title: string
  text: string
}

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function temperatures(weather: WeatherSnapshot, day: WeatherDay): number[] {
  // Amap's day/night temperatures are not QWeather's daily high/low temperatures.
  const values = weather.provider === 'qweather'
    ? [day.temp_max, day.temp_min] : [day.day_temp, day.night_temp]
  return values.filter(finite)
}

function windScale(value: string): number {
  const scales = value.match(/\d+(?:\.\d+)?/g)?.map(Number) || []
  return scales.length ? Math.max(...scales) : 0
}

export function weatherAdvice(weather: WeatherSnapshot | null | undefined, date: string): WeatherAdvice[] {
  if (!weather || !['available', 'partial'].includes(weather.status)) return []
  const day = weather.days.find(item => item.date === date && item.status === 'available')
  if (!day) return []

  const advice: WeatherAdvice[] = []
  const values = temperatures(weather, day)
  const high = values.length ? Math.max(...values) : null
  const low = values.length ? Math.min(...values) : null
  const conditions = day.day_weather + ' ' + day.night_weather
  const snow = /雪|冰粒|冻雨/.test(conditions)
  const rain = /雨/.test(conditions)
  const thunder = /雷/.test(conditions)
  const strongWind = Math.max(windScale(day.day_power), windScale(day.night_power)) >= 6

  if (high !== null && low !== null) {
    let text: string
    if (low <= 5) text = '建议穿保暖外套、长裤和包脚鞋，停留室外时按体感添衣。'
    else if (high - low >= 10) text = '气温跨度较大，建议分层穿搭并随身带外套，方便增减衣物。'
    else if (low <= 12) text = '建议长袖搭配保暖外套，外出时注意保暖。'
    else if (high >= 30) text = '建议穿轻薄透气衣物，长时间待在空调室内可备薄外套。'
    else if (low <= 18 || high <= 22) text = '建议长袖或随身带薄外套，按个人体感增减。'
    else text = '可选择轻便舒适的衣物与步行鞋，室内外转换时按体感调整。'
    advice.push({ kind: 'clothing', title: '穿衣建议', text })
  }

  if (snow) {
    advice.push({ kind: 'gear', title: '雨雪准备', text: '备好防水衣物和防滑鞋；路面可能湿滑或结冰，过台阶、坡道时放慢脚步。' })
    advice.push({ kind: 'activity', title: '活动安排', text: '可优先考虑博物馆、展馆等室内场所，减少长时间露天步行，留出交通缓冲。' })
  } else if (rain) {
    advice.push({ kind: 'gear', title: '雨天准备', text: strongWind
      ? '备好雨衣、防水收纳和防滑鞋；风大时谨慎撑伞，湿滑台阶与石板路请慢行。'
      : '随身带伞或雨衣，穿防滑鞋；湿滑台阶、石板路和临水路段请慢行。' })
    advice.push({ kind: 'activity', title: '活动安排', text: thunder
      ? '可优先选择室内场所；如遇雷电，及时进入安全室内，避免在树下或空旷高处停留。'
      : '可把博物馆、展馆等室内景点作为备选，减少连续露天活动；需调整时可使用行程修改。' })
  } else if (high !== null && high >= 30) {
    advice.push({ kind: 'gear', title: '随身准备', text: '带好饮水、遮阳帽和防晒用品，户外活动中适当补水休息。' })
    advice.push({ kind: 'activity', title: '活动安排', text: '露天游览与室内参观交替安排，减少长时间暴晒，预留休息时间。' })
  } else if (day.day_weather.includes('晴')) {
    advice.push({ kind: 'gear', title: '随身准备', text: '户外游览可备遮阳帽、防晒用品和饮水，步行途中适当休息。' })
  }

  if (strongWind) advice.push({ kind: 'wind', title: '风力提醒', text: '预报风力较大，固定好帽子等随身物品，谨慎选择高处、临水和无遮挡的活动。' })
  if (/雾|霾|沙尘|扬沙|浮尘/.test(conditions)) advice.push({ kind: 'visibility', title: '能见度提醒', text: '能见度可能受影响，驾车或步行过街请放慢速度，观景与交通安排适当留出余量。' })
  return advice
}
