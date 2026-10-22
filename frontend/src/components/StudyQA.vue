<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { MessageCircle, Send, LoaderCircle, X, ImagePlus, ClipboardPaste, Plus, Trash2 } from 'lucide-vue-next'
import { api } from '../api'
import { pastedPhoto, preparePhoto, readClipboardPhoto } from '../photoInput'
import { studyQuestions } from '../studySuggestions'
import type { ChatResult, StudyConversation } from '../types'
import StudyChatAnswer from './StudyChatAnswer.vue'

const props = defineProps<{ ready: boolean; planId?: string; placeId?: string; placeName?: string }>()
const question = ref(''), error = ref(''), notice = ref(''), busy = ref(false), restoring = ref(false)
const photo = ref<File | null>(null), preview = ref(''), stopName = ref(''), photoContext = ref(false)
const preparingPhoto = ref(false)
const conversation = ref<StudyConversation | null>(null), pendingQuestion = ref('')
const picker = ref<HTMLInputElement>(), chatLog = ref<HTMLElement>()
let controller: AbortController | null = null, sequence = 0, scope = '', initialized = false
let photoSequence = 0
const canSend = computed(() => !busy.value && !restoring.value && !preparingPhoto.value && (photo.value || ((props.ready || photoContext.value) && question.value.trim())))
const storageKey = (planId?: string) => 'travel-study-chat:' + (planId || 'standalone')
function remember() { if (conversation.value) sessionStorage.setItem(scope, conversation.value.id); else sessionStorage.removeItem(scope) }
function cancel() { sequence++; controller?.abort(); controller = null; busy.value = false; pendingQuestion.value = '' }
function clearPhoto() {
  if (preview.value) URL.revokeObjectURL(preview.value)
  photo.value = null; preview.value = ''
  if (picker.value) picker.value.value = ''
}
function removePhoto() { photoSequence++; preparingPhoto.value = false; clearPhoto() }
async function attachPhoto(source: File | Promise<File>) {
  if (busy.value || restoring.value) return
  const token = ++photoSequence
  preparingPhoto.value = true; error.value = ''
  try {
    const prepared = await preparePhoto(await source)
    if (token !== photoSequence) return
    clearPhoto()
    photo.value = prepared; preview.value = URL.createObjectURL(prepared)
    notice.value = '照片已添加，确认预览后再发送。'
  } catch (e) {
    if (token === photoSequence) error.value = e instanceof Error ? e.message : '照片读取失败，请重新选择或粘贴'
  } finally {
    if (token === photoSequence) { preparingPhoto.value = false; if (picker.value) picker.value.value = '' }
  }
}
function choosePhoto(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) void attachPhoto(file)
}
function pastePhoto(event: ClipboardEvent) {
  if (busy.value || restoring.value) return
  const file = pastedPhoto(event.clipboardData)
  if (!file) return
  event.preventDefault()
  void attachPhoto(file)
}
function pasteFromClipboard() {
  if (!busy.value && !restoring.value && !preparingPhoto.value) void attachPhoto(readClipboardPhoto())
}
function encode(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('照片读取失败，请重新选择'))
    reader.readAsDataURL(file)
  })
}
async function scrollChat() { await nextTick(); if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight }
async function refreshSession(id: string, token: number) {
  const value = await api<StudyConversation>('/api/study/conversations/' + encodeURIComponent(id))
  if (token !== sequence) return
  if (value.plan_id !== (props.planId || null)) throw new Error('对话所属行程已改变，请开始新对话')
  conversation.value = value; photoContext.value = value.photo_context_available; remember()
  await scrollChat()
}
watch(() => props.planId, async planId => {
  cancel(); removePhoto()
  if (initialized) sessionStorage.removeItem(scope)
  initialized = true; scope = storageKey(planId)
  conversation.value = null; photoContext.value = false; error.value = ''; notice.value = ''; question.value = ''; stopName.value = ''
  const saved = sessionStorage.getItem(scope), token = sequence
  restoring.value = true
  try {
    if (saved) { await refreshSession(saved, token); if (token === sequence) notice.value = '已恢复这段对话，可以接着提问。' }
  } catch (e) {
    if (token === sequence) { sessionStorage.removeItem(scope); notice.value = e instanceof Error ? e.message : '旧对话无法恢复，请开始新对话' }
  } finally { if (token === sequence) restoring.value = false }
}, { immediate: true })
watch(() => props.placeId, () => { if (!restoring.value) cancel(); removePhoto(); error.value = ''; stopName.value = ''; notice.value = '当前地点已更新，对话记录仍然保留。' })
onBeforeUnmount(() => { cancel(); removePhoto(); sessionStorage.removeItem(scope) })

async function newConversation(clear = false) {
  cancel(); restoring.value = false
  const old = conversation.value, token = sequence
  if (clear && old) {
    restoring.value = true
    try {
      const response = await fetch('/api/study/conversations/' + encodeURIComponent(old.id), { method: 'DELETE' })
      if (!response.ok) throw new Error('清空未完成，请稍后重试')
    } catch (e) { if (token === sequence) error.value = e instanceof Error ? e.message : '清空失败'; return }
    finally { if (token === sequence) restoring.value = false }
  }
  if (token !== sequence) return
  conversation.value = null; photoContext.value = false; question.value = ''; stopName.value = ''; error.value = ''; removePhoto(); remember()
  notice.value = clear ? '这段对话已清空。' : '已开始新对话。'
}

async function ask(sample?: string) {
  if (sample) { removePhoto(); photoContext.value = false; question.value = sample }
  if (!canSend.value) return
  const token = ++sequence, file = photo.value, place = props.placeName || stopName.value.trim()
  const asked = question.value.trim() || '请讲解照片中的主要对象与这个地点的历史联系，并给一个观察提示。'
  busy.value = true; error.value = ''; notice.value = ''; pendingQuestion.value = asked
  controller = new AbortController()
  const signal = controller.signal
  try {
    if (!conversation.value) {
      const value = await api<StudyConversation>('/api/study/conversations', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, signal, body: JSON.stringify({ plan_id: props.planId || null }) })
      if (token !== sequence) return
      conversation.value = value; remember()
    }
    const body = { question: asked, plan_id: props.planId || null, place_id: props.placeId || null,
      conversation_id: conversation.value.id, request_id: crypto.randomUUID(), expected_revision: conversation.value.revision,
      use_photo_context: photoContext.value }
    const image = file ? await encode(file) : null
    if (token !== sequence) return
    const result = await api<ChatResult>(file ? '/api/study/photo-ask' : '/api/study/ask', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify(file ? { ...body, image_base64: image, stop_name: place } : body) })
    if (token !== sequence) return
    await refreshSession(result.conversation_id, token)
    if (token !== sequence) return
    question.value = ''; removePhoto()
    await scrollChat()
  } catch (e) {
    if (token === sequence && !(e instanceof DOMException && e.name === 'AbortError')) {
      error.value = e instanceof Error ? e.message : '问答失败'
      // Recover a committed answer when its HTTP response was lost, without another model request.
      if (conversation.value) {
        try { await refreshSession(conversation.value.id, token) }
        catch { if (token === sequence) { conversation.value = null; photoContext.value = false; remember() } }
      }
    }
  } finally { if (token === sequence) { busy.value = false; pendingQuestion.value = ''; controller = null } }
}
</script>

<template>
  <section class="study-qa card" aria-label="历史资料问答">
    <div class="mini-heading"><MessageCircle :size="19" /><h3>问问沿途的故事</h3><span>沿着一个问题，接着聊</span></div>
    <div class="qa-session-toolbar"><span>{{ placeName ? '当前地点：' + placeName : '文字提问，也可以拍照问' }}</span><button type="button" :disabled="restoring" @click="newConversation()"><Plus :size="13" />新对话</button><button type="button" :disabled="!conversation || restoring" @click="newConversation(true)"><Trash2 :size="13" />清空对话</button></div>
    <p v-if="notice || restoring" class="study-policy" role="status">{{ restoring ? '正在恢复对话…' : notice }}</p>
    <div v-if="conversation?.turns.length || pendingQuestion" ref="chatLog" class="qa-chat-log" role="log" aria-label="研学对话记录" aria-live="polite">
      <article v-for="turn in conversation?.turns" :key="turn.id" class="qa-chat-turn"><div class="qa-user-message"><span v-if="turn.kind === 'photo'" class="qa-photo-badge"><ImagePlus :size="12" />已分析的照片</span><p>{{ turn.question }}</p><small v-if="turn.place_name">{{ turn.place_name }}</small></div><StudyChatAnswer :result="turn.result" /></article>
      <div v-if="pendingQuestion" class="qa-user-message qa-pending"><p>{{ pendingQuestion }}</p><small>正在回答…</small></div>
    </div>
    <div v-if="!conversation?.turns.length" class="qa-examples"><button v-for="example in studyQuestions" :key="example.question" type="button" :disabled="busy || !ready || restoring" @click="ask(example.question)">{{ example.label }}</button></div>
    <form class="qa-composer" @submit.prevent="ask()" @paste="pastePhoto">
      <div v-if="preparingPhoto" class="qa-photo-preparing" role="status"><LoaderCircle :size="15" class="spin" />正在准备照片…<button type="button" @click="removePhoto">取消</button></div>
      <div v-if="photo" class="qa-photo-preview"><img :src="preview" alt="待讲解的照片" /><div><strong>从照片里，发现历史线索</strong><p>{{ photo.name }}</p><small>清晰的展品或铭牌，讲解更有针对性</small></div><button type="button" class="icon-button" :disabled="busy" aria-label="移除照片" @click="removePhoto"><X :size="17" /></button></div>
      <label v-if="photo" class="qa-photo-place"><span>拍摄地点</span><span v-if="placeName" class="qa-selected-place">{{ placeName }}</span><input v-else v-model="stopName" maxlength="120" :disabled="busy" placeholder="例如：四行仓库（可选）" /></label>
      <div v-if="photoContext && !photo" class="qa-context-chip">接着聊上一张照片<button type="button" :disabled="busy" aria-label="移除照片上下文" @click="photoContext = false"><X :size="12" /></button></div>
      <textarea v-model="question" maxlength="1000" :disabled="busy || restoring" rows="2" :placeholder="photo ? '想了解照片里的什么？也可以直接发送…' : conversation?.turns.length ? '可以接着问：他、这里、刚才那台机器…' : '问问人物、事件、地点之间的联系…'" aria-label="历史资料问题" />
      <div class="qa-composer-actions"><input ref="picker" class="qa-file-input" type="file" accept=".jpg,.jpeg,.png,image/jpeg,image/png" :disabled="busy || restoring || preparingPhoto" aria-label="选择研学照片" @change="choosePhoto" /><button type="button" class="qa-attach" :disabled="busy || restoring || preparingPhoto" @click="picker?.click()"><ImagePlus :size="17" />添加照片</button><button type="button" class="qa-attach" :disabled="busy || restoring || preparingPhoto" @click="pasteFromClipboard"><ClipboardPaste :size="16" />粘贴图片</button><button v-if="busy" type="button" class="qa-stop" @click="cancel(); notice = '已停止等待；刷新对话可核对是否已完成。'">取消</button><button type="submit" class="qa-send" :disabled="!canSend" aria-label="提交资料问题"><LoaderCircle v-if="busy" :size="16" class="spin" /><Send v-else :size="16" /><span>{{ busy ? '正在解读' : '发送' }}</span></button></div>
      <p class="qa-photo-help">支持3MB以内JPG/PNG。在输入框按 Ctrl+V，或点击“粘贴图片”添加截图；预览后发送，提问不会修改行程。</p>
    </form>
    <p class="study-policy">对话保存在运行服务的电脑上。可以连续追问，每轮出处单独保留。</p>
    <p v-if="!ready && !photo && !photoContext" class="study-policy">本地资料尚未就绪；仍可添加照片，联网查找相关故事。</p>
    <p v-if="error" class="qa-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.qa-composer-actions { flex-wrap: wrap; row-gap: 9px; }
.qa-attach { white-space: nowrap; }
.qa-send { margin-left: auto; }
.qa-photo-preparing { display: flex; align-items: center; gap: 7px; font-size: 12px; color: #617d61; padding: 7px 12px 11px; }
.qa-photo-preparing > button { margin-left: auto; color: #79906b; text-decoration: underline; text-underline-offset: 3px; }
.qa-photo-help { margin: 9px 12px 12px; font-size: 11px; line-height: 1.8; color: #819078; }
</style>
