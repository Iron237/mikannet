<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const props = defineProps({ file: { type: Object, required: true }, title: String, number: Number })
const emit = defineEmits(['close'])
const dialog = ref(null)
const endpoint = `/api/files/${props.file.id}/danmaku`
const fileName = (props.file.path || props.file.name || '').split(/[\\/]/).pop()
const anime = ref(props.title || '')
const episode = ref(Number.isInteger(props.number) && props.number > 0 ? String(props.number) : '')
const configured = ref(null)
const choices = ref([])
const selectedId = ref('')
const selected = computed(() => choices.value.find(c => c.episode_id === selectedId.value))
const existing = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')
const controller = new AbortController()

async function lookup(manual = false) {
  busy.value = true
  error.value = ''
  selectedId.value = ''
  choices.value = []
  message.value = manual ? '正在搜索节目…' : '正在匹配视频…'
  try {
    const params = new URLSearchParams({ anime: anime.value.trim(), episode: episode.value.trim() })
    const url = manual ? `${endpoint}/search?${params}` : `${endpoint}/matches`
    const result = await api.get(url, controller.signal)
    if (!manual) {
      configured.value = result.configured
      existing.value = result.existing || ''
    } else configured.value = true
    choices.value = result.choices || []
    if (result.exact) selectedId.value = choices.value[0].episode_id
    if (existing.value) message.value = '视频旁已有同名 XML。'
    else if (!configured.value) message.value = '请在「设置 → 网络与元数据 → 弹幕」填写弹弹play应用凭据。'
    else if (result.exact) message.value = '已精确匹配，请核对节目和集名。'
    else message.value = choices.value.length ? '请选择与视频对应的节目和集数。' : '没有找到匹配的节目，可调整番名或集数再搜索。'
  } catch (e) {
    if (!controller.signal.aborted) {
      message.value = ''
      error.value = e.message
    }
  } finally { busy.value = false }
}

async function save(browser) {
  if (!selected.value || busy.value) return
  busy.value = true
  message.value = '正在下载弹幕…'
  error.value = ''
  const { episode_id, shift } = selected.value
  try {
    if (browser) {
      const blob = await api.postBlob(endpoint + '?browser=true', { episode_id, shift })
      if (controller.signal.aborted) return
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = fileName.replace(/\.[^.]+$/, '') + '.xml'
      link.click()
      setTimeout(() => URL.revokeObjectURL(link.href), 30000)
      message.value = 'XML 已交给浏览器下载。'
    } else {
      const result = await api.post(endpoint, { episode_id, shift })
      existing.value = result.existing
      message.value = `已保存 ${result.count} 条弹幕到视频旁。`
    }
  } catch (e) { message.value = ''; error.value = e.message }
  finally { busy.value = false }
}

onMounted(() => { dialog.value.showModal(); lookup() })
onUnmounted(() => controller.abort())
</script>

<template>
  <dialog ref="dialog" class="modal danmaku" aria-labelledby="danmaku-title" @close="emit('close')">
    <div class="row">
      <h3 id="danmaku-title">下载弹幕</h3>
      <span class="spacer" />
      <button class="btn sm" type="button" autofocus @click="dialog.close()">关闭</button>
    </div>
    <p class="filename">{{ fileName }}</p>
    <p role="status">{{ message }}</p>
    <p v-if="error" role="alert" class="err">{{ error }}</p>
    <a v-if="existing" class="btn" :href="existing" download>下载已有 XML</a>
    <template v-else-if="configured || (configured === null && error)">
      <form class="search" @submit.prevent="lookup(true)">
        <label for="danmaku-anime">番名</label>
        <input id="danmaku-anime" v-model="anime" class="input" required minlength="2" maxlength="160" />
        <label for="danmaku-episode">集数（选填）</label>
        <input id="danmaku-episode" v-model="episode" class="input" pattern="[CSO]?[1-9][0-9]{0,4}" placeholder="如 1 或 S1" />
        <button class="btn sm" :disabled="busy || anime.trim().length < 2">搜索节目</button>
      </form>
      <template v-if="choices.length">
        <label for="danmaku-choice">对应节目</label>
        <select id="danmaku-choice" v-model="selectedId" class="input" :disabled="busy">
          <option disabled value="">选择番剧和集名</option>
          <option v-for="c in choices" :key="c.episode_id" :value="c.episode_id">{{ c.anime }} · {{ c.episode }}</option>
        </select>
        <div class="actions">
          <button class="btn primary" :disabled="busy || !selected" @click="save(false)">保存到视频旁</button>
          <button class="btn" :disabled="busy || !selected" @click="save(true)">下载到浏览器</button>
        </div>
      </template>
    </template>
    <button v-if="error && configured === null" class="btn sm" :disabled="busy" @click="lookup()">重新匹配</button>
    <p class="muted credit">来源 · <a href="https://www.dandanplay.com/" target="_blank" rel="noopener noreferrer">弹弹play开放弹幕网络</a></p>
  </dialog>
</template>

<style scoped>
.danmaku { margin: auto; width: min(32rem, 94vw); max-height: 88vh; color: var(--text); }
.danmaku::backdrop { background: rgb(0 0 0 / .55); }
.danmaku p { margin: .8rem 0; }
.filename { overflow-wrap: anywhere; color: var(--text-dim); }
.search { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: .5rem; margin-bottom: 1rem; }
.search button { grid-column: 2; justify-self: start; }
select { width: 100%; margin: .4rem 0; }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .8rem; }
.credit { font-size: .8rem; }
.err { color: var(--red); }
</style>
