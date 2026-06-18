<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'
import { requestNative } from '../native'

const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR', 'DEBUG']
const level = ref('ALL')
const lines = ref([])
const logsDir = ref(null)   // { reveal_url, configured, archive_count }
const paused = ref(false)
let timer = null

const levelClass = { INFO: 'lv-info', WARNING: 'lv-warn', ERROR: 'lv-err', DEBUG: 'lv-dbg' }

async function refresh() {
  if (paused.value) return
  try {
    lines.value = await api.get(`/api/logs?level=${level.value}&limit=800`)
  } catch { /* 忽略 */ }
}
function setLevel(l) { level.value = l; refresh() }

function openLogDir() { if (logsDir.value?.reveal_url) requestNative(logsDir.value.reveal_url) }

async function copyAll() {
  const text = lines.value.map(l =>
    `${new Date(l.ts).toLocaleString('zh-CN')} ${l.level} ${l.logger}: ${l.msg}`).join('\n')
  try { await navigator.clipboard.writeText(text) } catch { /* ignore */ }
}

onMounted(async () => {
  refresh()
  try { logsDir.value = await api.get('/api/logs/dir') } catch { /* ignore */ }
  timer = setInterval(refresh, 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 14px;">
      <div class="page-title" style="margin: 0;">日志</div>
      <div class="spacer" />
      <button class="btn sm" :class="{ primary: paused }" @click="paused = !paused">
        {{ paused ? '已暂停' : '实时' }}
      </button>
      <button class="btn sm" @click="copyAll"><Icon name="copy" :size="13" /> 复制</button>
    </div>

    <div class="tabs">
      <button v-for="l in LEVELS" :key="l" class="tab" :class="{ on: level === l }" @click="setLevel(l)">
        {{ l === 'ALL' ? '全部' : l }}
      </button>
      <div class="spacer" />
      <span class="muted" style="font-size: 12px;">{{ lines.length }} 条</span>
    </div>

    <div class="logbox">
      <div v-for="(l, i) in lines" :key="i" class="logline">
        <span class="lv" :class="levelClass[l.level]">{{ l.level }}</span>
        <span class="ts">{{ new Date(l.ts).toLocaleTimeString('zh-CN') }}</span>
        <span class="lg">{{ l.logger }}</span>
        <span class="msg">{{ l.msg }}</span>
      </div>
      <div v-if="!lines.length" class="muted" style="padding: 20px;">暂无日志</div>
    </div>

    <div class="card logdir" style="margin-top: 14px;">
      <Icon name="folder-open" :size="14" class="muted" />
      <span class="muted">历史日志(重启时压缩,全部保留<span v-if="logsDir?.archive_count">,{{ logsDir.archive_count }} 个归档</span>)都在 log 目录里,按文件名时间排列。</span>
      <div class="spacer" />
      <button class="btn sm" :disabled="!logsDir?.reveal_url" title="在资源管理器中定位 log 目录" @click="openLogDir">
        <Icon name="folder-open" :size="13" /> 打开 log 目录
      </button>
    </div>
    <div v-if="logsDir && !logsDir.reveal_url" class="muted logdir-hint">
      「打开 log 目录」需配置:设置 → 播放 填「data 目录宿主机根」并安装协议处理器(mikanarr://)后可一键打开。
    </div>
  </div>
</template>

<style scoped>
.tabs { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; }
.tab {
  padding: 4px 12px; border-radius: 8px; font-size: 12.5px; cursor: pointer;
  border: 1px solid var(--border); background: var(--bg-card); color: var(--text-dim);
}
.tab.on { background: var(--accent); border-color: var(--accent); color: #1a1207; font-weight: 700; }
.logbox {
  background: #0b0e14; border: 1px solid var(--border); border-radius: 10px;
  padding: 8px 10px; height: 64vh; overflow-y: auto; font-size: 12px;
  font-family: "Cascadia Code", Consolas, monospace; line-height: 1.6;
}
.logline { display: flex; gap: 8px; white-space: pre-wrap; word-break: break-all; padding: 1px 0; }
.lv { flex-shrink: 0; width: 58px; font-weight: 700; }
.lv-info { color: var(--blue); } .lv-warn { color: var(--accent); }
.lv-err { color: var(--red); } .lv-dbg { color: var(--text-dim); }
.ts { flex-shrink: 0; color: var(--text-dim); }
.lg { flex-shrink: 0; color: #7aa2c4; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.msg { flex: 1; }
.logdir { display: flex; align-items: center; gap: 8px; padding: 10px 16px; font-size: 12.5px; }
.logdir-hint { font-size: 12px; margin-top: 8px; }

@media (max-width: 768px) {
  /* 手机端日志行换行:级别/时间/logger 占首行,消息整段另起一行铺满(不再一字一行)*/
  .logbox { font-size: 11px; height: 60vh; }
  .logline { flex-wrap: wrap; gap: 6px; }
  .lg { max-width: 100%; }
  .msg { flex-basis: 100%; }
}
</style>
