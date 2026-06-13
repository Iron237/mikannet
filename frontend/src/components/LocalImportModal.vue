<script setup>
import { onUnmounted, ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['close'])

const path = ref('/import')
const scanning = ref(false)
const groups = ref(null)      // scan 结果 + checked + mikan_id_input
const status = ref(null)
const error = ref('')
let pollTimer = null

async function doScan() {
  scanning.value = true
  error.value = ''
  try {
    const r = await api.post('/api/import/local/scan', { path: path.value })
    groups.value = r.map(g => ({
      ...g, checked: !!g.mikan,
      mikan_id_input: g.mikan?.mikan_bangumi_id ?? '',
    }))
  } catch (e) { error.value = e.message }
  scanning.value = false
}

function bulk(v) {
  for (const g of groups.value) g.checked = v
}

async function run() {
  error.value = ''
  const selected = groups.value.filter(g => g.checked).map(g => ({
    guess_title: g.guess_title,
    files: g.files,
    mikan: g.mikan_id_input
      ? { mikan_bangumi_id: Number(g.mikan_id_input), title: g.mikan?.title ?? g.guess_title }
      : null,
  }))
  if (!selected.length) { error.value = '没有勾选任何分组'; return }
  try {
    await api.post('/api/import/local/run', { groups: selected })
    poll()
  } catch (e) { error.value = e.message }
}

async function poll() {
  status.value = await api.get('/api/import/local/status')
  if (status.value.running) pollTimer = setTimeout(poll, 2000)
}

onUnmounted(() => clearTimeout(pollTimer))
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" style="width: min(860px, 94vw);">
      <div class="row" style="margin-bottom: 10px;">
        <h3>导入本地番剧</h3>
        <div class="spacer" />
        <button class="btn sm" @click="emit('close')">✕</button>
      </div>
      <p class="muted" style="font-size: 12.5px; margin-bottom: 12px;">
        扫描目录里的视频文件,按作品自动分组并匹配蜜柑番剧;确认后**移动**到
        Mikanarr 管理目录(NAS)、提取文件信息并同步封面/元数据到番剧库。
        目录需先挂载:在 <code>.env</code> 设 <code>LOCAL_IMPORT_PATH=你的旧番剧文件夹</code> 后
        <code>docker compose up -d</code>,容器内对应 <code>/import</code>。
      </p>

      <div class="row" style="margin-bottom: 12px;">
        <input v-model="path" class="input" placeholder="/import" />
        <button class="btn primary" :disabled="scanning" @click="doScan">
          {{ scanning ? '扫描中…' : '扫描' }}
        </button>
      </div>
      <p v-if="error" style="color: var(--red); font-size: 12.5px; margin-bottom: 8px;">{{ error }}</p>

      <template v-if="groups">
        <div class="row" style="margin-bottom: 8px;">
          <span class="muted" style="font-size: 12.5px;">
            识别出 {{ groups.length }} 个作品,已勾选 {{ groups.filter(g => g.checked).length }} 个
          </span>
          <div class="spacer" />
          <button class="btn sm" @click="bulk(true)">全选</button>
          <button class="btn sm" @click="bulk(false)">清空</button>
        </div>
        <div class="group-list">
          <div v-for="g in groups" :key="g.guess_title" class="g-item" :class="{ off: !g.checked }">
            <label class="row" style="cursor: pointer;">
              <input type="checkbox" v-model="g.checked" />
              <strong>{{ g.guess_title }}</strong>
              <span class="tag">{{ g.files.length }} 个文件</span>
              <span class="muted" style="font-size: 11.5px;" v-if="g.episodes.length">
                第 {{ g.episodes[0] }}-{{ g.episodes[g.episodes.length - 1] }} 话
              </span>
            </label>
            <div class="row" style="margin-top: 6px; padding-left: 26px;">
              <span class="muted" style="font-size: 12px;">蜜柑匹配:</span>
              <span v-if="g.mikan" class="tag green">{{ g.mikan.title }}</span>
              <span v-else class="tag red">未匹配</span>
              <input v-model="g.mikan_id_input" class="input" style="width: 130px;"
                     placeholder="番剧 ID 手动指定" title="mikanani.me/Home/Bangumi/{ID} 里的数字" />
            </div>
          </div>
        </div>
        <div class="row" style="justify-content: flex-end; margin-top: 12px;">
          <button class="btn primary" :disabled="status?.running" @click="run">
            {{ status?.running ? '导入中…' : '开始导入(移动文件)' }}
          </button>
        </div>
      </template>

      <div v-if="status" class="card" style="margin-top: 10px; padding: 12px; font-size: 12.5px;">
        <div>{{ status.running ? '导入中…' : '导入完成' }} {{ status.done }}/{{ status.total }}</div>
        <div v-for="s in status.imported" :key="s" class="muted">✓ {{ s }}</div>
        <div v-for="e in status.errors" :key="e" style="color: var(--red);">✕ {{ e }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.group-list { max-height: 44vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.g-item { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.g-item.off { opacity: .5; }
.g-item input[type=checkbox] { accent-color: var(--accent); }
</style>
