const MAX_UPLOAD_BYTES = 3_000_000
const PHOTO_EXTENSION = /\.(?:jpe?g|png)$/i

export function isPhotoFile(file: File): boolean {
  return ['image/jpeg', 'image/png'].includes(file.type) || PHOTO_EXTENSION.test(file.name)
}

export function pastedPhoto(data: DataTransfer | null): File | null {
  if (!data) return null
  for (const item of Array.from(data.items)) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) return file
    }
  }
  return Array.from(data.files).find(isPhotoFile) || null
}

export async function readClipboardPhoto(): Promise<File> {
  const hint = '请复制图片或截图，再点击问题输入框按 Ctrl+V 粘贴。'
  if (!navigator.clipboard?.read) throw new Error(hint)
  let items: ClipboardItem[]
  try { items = await navigator.clipboard.read() }
  catch { throw new Error('无法读取剪贴板。' + hint) }
  for (const item of items) {
    const type = item.types.find(value => value === 'image/png') || item.types.find(value => value === 'image/jpeg')
    if (type) {
      const blob = await item.getType(type)
      return new File([blob], '粘贴图片.' + (type === 'image/jpeg' ? 'jpg' : type.split('/')[1] || 'png'), { type })
    }
  }
  throw new Error('剪贴板中没有JPG或PNG图片。' + hint)
}

export async function preparePhoto(source: File): Promise<File> {
  if (!source.size) throw new Error('图片内容为空，请重新选择或复制。')
  if (source.size > MAX_UPLOAD_BYTES) throw new Error('请选择3MB以内的JPG或PNG照片。')
  const header = new Uint8Array(await source.slice(0, 8).arrayBuffer())
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((byte, index) => header[index] === byte)
  const jpeg = header[0] === 255 && header[1] === 216 && header[2] === 255
  const mime = png ? 'image/png' : jpeg ? 'image/jpeg' : ''
  if (!mime) throw new Error('仅支持JPG或PNG照片，请检查图片格式。')

  // Inspect actual bytes so a missing or incorrect OS MIME label does not reject valid photos.
  return source.type !== mime ? new File([source], source.name, { type: mime }) : source
}
