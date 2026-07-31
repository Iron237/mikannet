<script setup>
import { computed, ref, watch } from 'vue'
import { api, fmtSize } from '../api'
import Icon from './Icon.vue'

const props = defineProps({
  open: Boolean,
  initialPath: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const current = ref('')
const result = ref(null)
const busy = ref(false)
const error = ref('')
const newFolder = ref('')

const crumbs = computed(() => {
  const out = [{ name: '媒体库', path: '' }]
  const parts = current.value ? current.value.split('/') : []
  parts.forEach((name, index) => out.push({ name, path: parts.slice(0, index + 1).join('/') }))
  return out
})

async function load(path = current.value) {
  busy.value = true
  error.value = ''
  try {
    result.value = await api.get(`/api/files/browse?path=${encodeURIComponent(path || '')}`)
    current.value = result.value.path || ''
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function mkdir() {
  const name = newFolder.value.trim()
  if (!name) return
  try {
    await api.post('/api/files/mkdir', { parent: current.value, name })
    newFolder.value = ''
    await load()
  } catch (e) { error.value = e.message }
}

function enter(entry) {
  if (entry.is_dir) load(entry.path)
}

watch(() => props.open, (shown) => {
  if (!shown) return
  current.value = props.initialPath || ''
  load(current.value)
}, { immediate: true })
</script>

<template>
  <div v-if="open" class="modal-mask" @click.self="emit('close')">
    <div class="modal browser-modal">
      <div class="row browser-head">
        <div>
          <h3>网页文件管理</h3>
          <div class="muted">仅显示服务器已挂载的媒体目录，不会访问其他主机路径。</div>
        </div>
        <div class="spacer" />
        <button class="btn sm" @click="load()"><Icon name="refresh" :size="13" /> 刷新</button>
        <button class="btn icon-only" aria-label="关闭文件管理" @click="emit('close')">×</button>
      </div>

      <div class="crumbs">
        <template v-for="(crumb, index) in crumbs" :key="crumb.path">
          <button class="crumb" @click="load(crumb.path)">{{ crumb.name }}</button>
          <span v-if="index < crumbs.length - 1">/</span>
        </template>
      </div>

      <div class="row create-row">
        <input v-model="newFolder" class="input" placeholder="新文件夹名称" @keyup.enter="mkdir" />
        <button class="btn sm" :disabled="!newFolder.trim()" @click="mkdir">
          <Icon name="folder-in" :size="13" /> 新建文件夹
        </button>
      </div>

      <div v-if="error" class="browser-error">{{ error }}</div>
      <div class="file-list">
        <button v-if="result?.parent !== null" class="file-row folder" @click="load(result.parent || '')">
          <Icon name="folder-open" :size="16" /><span>..</span><span class="muted">上一级</span>
        </button>
        <div v-for="entry in result?.entries || []" :key="entry.path" class="file-row"
             :class="{ folder: entry.is_dir }">
          <button class="entry-main" @click="enter(entry)">
            <Icon :name="entry.is_dir ? 'folder' : (entry.is_video ? 'play' : 'file')" :size="16" />
            <span class="entry-name" :title="entry.name">{{ entry.name }}</span>
          </button>
          <span v-if="!entry.is_dir" class="muted size">{{ fmtSize(entry.size) }}</span>
          <template v-if="!entry.is_dir">
            <a v-if="entry.is_video" class="btn xs" :href="entry.content_url" target="_blank" rel="noopener">
              网页打开
            </a>
            <a class="btn xs" :href="entry.content_url + '&download=true'">下载</a>
          </template>
        </div>
        <div v-if="busy" class="muted empty">读取中…</div>
        <div v-else-if="result && !result.entries.length" class="muted empty">此目录为空</div>
      </div>
      <div v-if="result?.truncated" class="muted truncate-note">目录超过 1000 项，仅显示前 1000 项。</div>
    </div>
  </div>
</template>

<style scoped>
.browser-modal { width: min(820px, calc(100vw - 28px)); }
.browser-head { gap: 8px; margin-bottom: 12px; }
.browser-head h3 { margin: 0 0 3px; }
.browser-head .muted { font-size: 12px; }
.icon-only { font-size: 22px; line-height: 1; padding: 3px 9px; }
.crumbs { display: flex; gap: 5px; flex-wrap: wrap; align-items: center; padding: 8px 10px;
  background: rgba(127,127,127,.08); border-radius: 7px; font-size: 12.5px; }
.crumb { border: 0; background: none; color: var(--accent); cursor: pointer; padding: 0; }
.create-row { gap: 8px; margin: 10px 0; }
.create-row .input { max-width: 280px; }
.browser-error { color: var(--red); font-size: 12.5px; margin: 8px 0; }
.file-list { border: 1px solid var(--border); border-radius: 8px; max-height: 58vh; overflow: auto; }
.file-row { min-height: 42px; display: flex; align-items: center; gap: 8px; padding: 5px 9px;
  border-bottom: 1px solid var(--border); }
.file-row:last-child { border-bottom: 0; }
.entry-main { min-width: 0; flex: 1; display: flex; align-items: center; gap: 8px; border: 0;
  background: none; color: var(--text); cursor: default; text-align: left; padding: 5px 0; }
.folder .entry-main, button.file-row.folder { cursor: pointer; }
button.file-row { width: 100%; background: none; color: var(--text); border-left: 0; border-right: 0; border-top: 0; }
.entry-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.size { font-size: 11.5px; min-width: 72px; text-align: right; }
.empty { padding: 24px; text-align: center; }
.truncate-note { font-size: 11.5px; margin-top: 7px; }
@media (max-width: 560px) {
  .size { display: none; }
  .browser-head > .muted { display: none; }
}
</style>
