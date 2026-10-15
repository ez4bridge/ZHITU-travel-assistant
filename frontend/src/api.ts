export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : '请求失败，请检查填写内容或稍后重试')
  }
  return response.json() as Promise<T>
}

export async function stream(path: string, body: unknown, signal: AbortSignal,
  onEvent: (kind: string, data: unknown) => void) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求未被接受，请检查填写内容')
  }
  if (!response.body) throw new Error('浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', receivedResult = false
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const kind = block.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim()
        const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (kind && data) {
          const parsed = JSON.parse(data)
          if (kind === 'error') throw new Error(parsed.message || '生成失败')
          if (kind === 'result') receivedResult = true
          onEvent(kind, parsed)
        }
      }
      if (done) break
    }
    if (!receivedResult) throw new Error('连接提前结束，请重试。已有方案仍然保留。')
  } finally {
    reader.releaseLock()
  }
}
