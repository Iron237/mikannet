<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from './Icon.vue'

const emit = defineEmits(['close'])

const path = ref('/import')
const scanning = ref(false)
const scanStatus = ref(null)
const groups = ref(null)      // scan 结果 + checked + bgm_id_input
const status = ref(null)
const error = ref('')
let pollTimer = null
let scanTimer = null

const scanPct = computed(() => {
  const s = scanStatus.value
  if (!s || !s.total) return 0
  return Math.round((s.done / s.total) * 100)
})

async function doScan() {
  scanning.value = true
  error.value = ''
  groups.value = null
  scanStatus.value = null
  try {
    await api.post('/api/import/local/scan', { path: path.value })
    pollScan()
  } catch (e) { error.value = e.message; scanning.value = false }
}

async function pollScan() {
  try {
    const s = await api.get('/api/import/local/scan/status')
    scanStatus.value = s
    if (s.running) { scanTimer = setTimeout(pollScan, 800); return }
    scanning.value = false
    if (s.error) { error.value = s.error; return }
    if (s.result) {
      groups.value = s.result.map(g => ({
        ...g, checked: !!g.bgm, bgm_id_input: g.bgm?.bgmtv_subject_id ?? '',
      }))
    }
  } catch (e) { error.value = e.message; scanning.value = false }
}

function bulk(v) {
  for (const g of groups.value) g.checked = v
}

async function run() {
  error.value = ''
  const selected = groups.value.filter(g => g.checked).map(g => ({
    guess_title: g.guess_title,
    files: g.files,
    bgm: g.bgm_id_input
      ? { bgmtv_subject_id: Number(g.bgm_id_input), title: g.bgm?.title ?? g.guess_title }
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

onUnmounted(() => { clearTimeout(pollTimer); clearTimeout(scanTimer) })
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" style="width: min(860px, 94vw);">
      <div class="row" style="margin-bottom: 10px;">
        <h3>导入本地番剧</h3>
        <div class="spacer" />
        <button class="btn sm" @click="emit('close')"><Icon name="close" :size="13" /></button>
      </div>
      <p class="muted" style="font-size: 12.5px; margin-bottom: 12px;">
        扫描目录里的视频文件,按作品自动分组并匹配 bgm.tv 番剧(中文/日文名都能搜到);
        确认后**移动**到 Mikannet 管理目录(NAS)、提取文件信息并同步封面/元数据到番剧库。
        来源两种:<b>本机磁盘</b>(.env <code>LOCAL_IMPORT_PATH</code> → <code>/import</code>)、
        <b>NAS</b>(.env <code>NAS_IMPORT_PATH</code> → <code>/import-nas</code>)。
        可直接粘贴 Windows 路径(<code>G:\Anime\X</code>)或 NAS 路径(<code>\\192.168.2.4\…\X</code>),
        系统会自动转换到对应目录。
      </p>

      <div class="row" style="margin-bottom: 12px; flex-wrap: wrap;">
        <button class="btn sm" @click="path = '/import'"><Icon name="folder" :size="13" /> 本机磁盘</button>
        <button class="btn sm" @click="path = '/import-nas'"><Icon name="database" :size="13" /> NAS</button>
        <input v-model="path" class="input" placeholder="/import 或 /import-nas 或粘贴 Win/NAS 路径" />
        <button class="btn primary" :disabled="scanning" @click="doScan">
          {{ scanning ? '扫描中…' : '扫描' }}
        </button>
      </div>
      <div v-if="scanning && scanStatus" class="scan-prog">
        <div class="bar" :class="{ indet: !scanStatus.total }">
          <div class="fill" :style="{ width: scanPct + '%' }" />
        </div>
        <div class="scan-info muted">
          <span class="ph">{{ scanStatus.phase || '准备中' }}</span>
          · 发现 <b>{{ scanStatus.files_found }}</b> 个视频
          <span v-if="scanStatus.total"> · 匹配蜜柑 <b>{{ scanStatus.done }}/{{ scanStatus.total }}</b> ({{ scanPct }}%)</span>
          <span v-if="scanStatus.current" class="cur"> · {{ scanStatus.current }}</span>
        </div>
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
              <span class="muted" style="font-size: 12px;">bgm.tv 匹配:</span>
              <span v-if="g.bgm" class="tag green">{{ g.bgm.title }}</span>
              <span v-else class="tag red">未匹配</span>
              <input v-model="g.bgm_id_input" class="input" style="width: 150px;"
                     placeholder="bgm.tv subject ID 手动指定" title="bgm.tv/subject/{ID} 里的数字" />
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
        <div v-for="s in status.imported" :key="s" class="muted"><Icon name="check" :size="12" /> {{ s }}</div>
        <div v-for="e in status.errors" :key="e" style="color: var(--red);"><Icon name="close" :size="12" /> {{ e }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scan-prog { margin-bottom: 12px; }
.scan-prog .bar { height: 8px; border-radius: 5px; background: #252d3d; overflow: hidden; }
.scan-prog .fill { height: 100%; background: var(--accent); transition: width .4s; }
.scan-prog .bar.indet .fill {
  width: 35% !important; background: linear-gradient(90deg, transparent, var(--accent), transparent);
  animation: indet 1.1s infinite linear;
}
@keyframes indet { 0% { margin-left: -35%; } 100% { margin-left: 100%; } }
.scan-info { font-size: 11.5px; margin-top: 5px; line-height: 1.5; }
.scan-info .ph { color: var(--accent); }
.scan-info .cur { color: var(--text-dim); word-break: break-all; }
.group-list { max-height: 44vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.g-item { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.g-item.off { opacity: .5; }
.g-item input[type=checkbox] { accent-color: var(--accent); }
</style>
