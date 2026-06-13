<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import EditSubscriptionModal from '../components/EditSubscriptionModal.vue'
import LocalImportModal from '../components/LocalImportModal.vue'
import SubscribeWizard from '../components/SubscribeWizard.vue'

const subs = ref([])
const showWizard = ref(false)
const editing = ref(null)
const showImport = ref(false)
const showLocalImport = ref(false)
const importUrl = ref('')
const importBackfill = ref(false)
const importStatus = ref(null)
const importError = ref('')
const selected = ref(new Set())
const delConfirm = ref(null)     // { ids: [...] } 待确认删除
const delFiles = ref(false)
const busy = ref(false)
let pollTimer = null

const allSelected = computed(() => subs.value.length > 0 && selected.value.size === subs.value.length)

function isSel(id) { return selected.value.has(id) }
function toggleSel(id) {
  const s = new Set(selected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selected.value = s
}
function selectAll() { selected.value = new Set(subs.value.map(s => s.id)) }
function clearSel() { selected.value = new Set() }

async function load() {
  subs.value = await api.get('/api/subscriptions')
  const ids = new Set(subs.value.map(s => s.id))
  selected.value = new Set([...selected.value].filter(id => ids.has(id)))
}

async function toggle(sub) {
  await api.patch(`/api/subscriptions/${sub.id}`, { enabled: !sub.enabled })
  await load()
}

async function doDelete() {
  busy.value = true
  try {
    await api.post('/api/subscriptions/batch-delete',
      { ids: delConfirm.value.ids, delete_files: delFiles.value })
    delConfirm.value = null; delFiles.value = false; clearSel()
    await load()
  } finally { busy.value = false }
}

async function startImport() {
  importError.value = ''
  try {
    await api.post('/api/import/mikan', { rss_url: importUrl.value, backfill: importBackfill.value })
    pollImport()
  } catch (e) { importError.value = e.message }
}

async function pollImport() {
  importStatus.value = await api.get('/api/import/mikan/status')
  await load()
  if (importStatus.value.running) {
    pollTimer = setTimeout(pollImport, 2000)
  }
}

onMounted(load)
onUnmounted(() => clearTimeout(pollTimer))
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 18px;">
      <div class="page-title" style="margin: 0;">订阅管理</div>
      <div class="spacer" />
      <button v-if="subs.length" class="btn sm" @click="allSelected ? clearSel() : selectAll()">
        {{ allSelected ? '取消全选' : '全选' }}
      </button>
      <button v-if="subs.length" class="btn sm" :disabled="!selected.size" @click="clearSel">清空选择</button>
      <button class="btn" @click="showLocalImport = true">📂 导入本地番剧</button>
      <button class="btn" @click="showImport = true">⇪ 导入蜜柑订阅</button>
      <button class="btn primary" @click="showWizard = true">＋ 添加订阅</button>
    </div>

    <div v-if="selected.size" class="batch-bar card">
      <strong>已选 {{ selected.size }} 个订阅</strong>
      <div class="spacer" />
      <button class="btn sm danger" :disabled="busy" @click="delConfirm = { ids: [...selected] }">
        🗑 批量删除
      </button>
    </div>

    <div v-if="!subs.length" class="card" style="text-align: center; padding: 50px; color: var(--text-dim);">
      还没有订阅 — 点击右上角「添加订阅」搜索蜜柑番剧
    </div>

    <div v-for="s in subs" :key="s.id" class="card sub-row" :class="{ sel: isSel(s.id) }">
      <div class="row">
        <input type="checkbox" class="ck" :checked="isSel(s.id)" @change="toggleSel(s.id)" />
        <span class="health" :class="s.last_poll_ok ? 'ok' : 'bad'"
              :title="s.last_poll_error || 'RSS 正常'">●</span>
        <div>
          <div style="font-weight: 600;">{{ s.bangumi_title }}</div>
          <div class="muted" style="font-size: 12.5px; margin-top: 2px;">
            {{ s.subgroup_name || '字幕组 #' + s.mikan_subgroup_id }}
            <span v-if="s.include_keywords.length"> · 包含: {{ s.include_keywords.join(' + ') }}</span>
            <span v-if="s.exclude_keywords.length"> · 排除: {{ s.exclude_keywords.join(' / ') }}</span>
            <span> · {{ s.exclude_batch ? '排除合集' : '允许合集' }}</span>
            <span> · {{ s.backfill ? '补齐历史' : '只追新' }}</span>
          </div>
        </div>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;" v-if="s.last_checked_at">
          上次检查 {{ new Date(s.last_checked_at + 'Z').toLocaleString('zh-CN') }}
        </span>
        <button class="btn sm" @click="editing = s">✎ 编辑规则</button>
        <button class="btn sm" :class="{ primary: s.enabled }" @click="toggle(s)">
          {{ s.enabled ? '已启用' : '已停用' }}
        </button>
        <button class="btn sm danger" @click="delConfirm = { ids: [s.id] }">🗑 删除</button>
      </div>
    </div>

    <div v-if="delConfirm" class="modal-mask" @click.self="delConfirm = null">
      <div class="modal" style="width: 440px;">
        <h3 style="margin-bottom: 10px;">删除 {{ delConfirm.ids.length }} 个订阅</h3>
        <p class="muted" style="font-size: 12.5px;">
          将移除订阅及其下载任务记录(番剧与剧集保留)。
        </p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="delFiles" />
          同时删除已下载的文件
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="delConfirm = null">取消</button>
          <button class="btn danger" :disabled="busy" @click="doDelete">确认删除</button>
        </div>
      </div>
    </div>

    <SubscribeWizard v-if="showWizard" @close="showWizard = false; load()" />
    <EditSubscriptionModal v-if="editing" :sub="editing" @close="editing = null; load()" />
    <LocalImportModal v-if="showLocalImport" @close="showLocalImport = false; load()" />

    <div v-if="showImport" class="modal-mask" @click.self="showImport = false">
      <div class="modal" style="width: 540px;">
        <h3 style="margin-bottom: 8px;">导入蜜柑订阅</h3>
        <p class="muted" style="font-size: 12.5px; margin-bottom: 14px;">
          登录 mikanani.me → 首页「RSS 订阅」拿到个人聚合链接(形如
          <code>https://mikanani.me/RSS/MyBangumi?token=…</code>),粘贴到下面。
          系统会反查每个条目对应的番剧和字幕组,自动批量建订阅。
        </p>
        <input v-model="importUrl" class="input" placeholder="https://mikanani.me/RSS/MyBangumi?token=…" />
        <label class="row" style="margin: 12px 0; cursor: pointer; font-size: 13px;">
          <input type="checkbox" v-model="importBackfill" />
          导入的订阅补齐全部历史剧集(不勾 = 只追新,推荐)
        </label>
        <p v-if="importError" style="color: var(--red); font-size: 12.5px;">{{ importError }}</p>
        <div v-if="importStatus" class="card" style="margin: 10px 0; padding: 12px; font-size: 12.5px;">
          <div>{{ importStatus.running ? '导入中…' : '导入完成' }}
            进度 {{ importStatus.done }}/{{ importStatus.total }},
            新建 {{ importStatus.created.length }} 个订阅
            <span v-if="importStatus.errors">,失败 {{ importStatus.errors }}</span>
          </div>
          <div v-for="c in importStatus.created" :key="c" class="muted">✓ {{ c }}</div>
        </div>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="showImport = false">关闭</button>
          <button class="btn primary" :disabled="importStatus?.running" @click="startImport">
            {{ importStatus?.running ? '导入中…' : '开始导入' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub-row { margin-bottom: 10px; padding: 14px 18px; }
.sub-row.sel { border-color: var(--accent); }
.ck { accent-color: var(--accent); flex-shrink: 0; cursor: pointer; }
.health { font-size: 10px; }
.health.ok { color: var(--green); }
.health.bad { color: var(--red); }
.batch-bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 16px; margin-bottom: 12px;
  position: sticky; top: 8px; z-index: 10; border-color: var(--accent-dim);
}
</style>
