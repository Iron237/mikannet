<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'

const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR', 'DEBUG']
const level = ref('ALL')
const lines = ref([])
const archives = ref([])
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

async function loadArchives() {
  try { archives.value = await api.get('/api/logs/archives') } catch { /* ignore */ }
}

async function copyAll() {
  const text = lines.value.map(l =>
    `${new Date(l.ts).toLocaleString('zh-CN')} ${l.level} ${l.logger}: ${l.msg}`).join('\n')
  try { await navigator.clipboard.writeText(text) } catch { /* ignore */ }
}

onMounted(() => {
  refresh(); loadArchives()
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

    <div v-if="archives.length" class="card" style="margin-top: 14px;">
      <h4 style="margin: 0 0 10px;">历史归档(重启时压缩,全部保留)</h4>
      <div class="arch">
        <a v-for="a in archives" :key="a" class="btn sm" :href="`/api/logs/archives/${a}`" download>
          <Icon name="download" :size="13" /> {{ a }}
        </a>
      </div>
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
.arch { display: flex; flex-wrap: wrap; gap: 8px; }
</style>
