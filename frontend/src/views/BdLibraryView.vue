<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'
import BdReleases from '../components/BdReleases.vue'

const releases = ref([])
const bangumiList = ref([])
const scan = ref(null)
const filter = ref('all')          // all | owned | unowned | unbound
const expanded = ref(new Set())
let scanTimer = null
let mounted = true

async function reload() { releases.value = await api.get('/api/bd/releases') }

onMounted(async () => {
  await reload()
  bangumiList.value = await api.get('/api/bangumi')
  const s = await api.get('/api/bd/scan/status')
  if (s.running) { scan.value = s; poll() }
})
onUnmounted(() => { mounted = false; clearTimeout(scanTimer) })

const shown = computed(() => releases.value.filter(r =>
  filter.value === 'all' ? true
    : filter.value === 'owned' ? r.owned
      : filter.value === 'unowned' ? !r.owned
        : !r.bangumi_id))
const counts = computed(() => ({
  all: releases.value.length,
  owned: releases.value.filter(r => r.owned).length,
  unbound: releases.value.filter(r => !r.bangumi_id).length,
}))

async function startScan() {
  try { await api.post('/api/bd/scan', {}); poll() }
  catch (e) { scan.value = { error: e.message } }
}
async function poll() {
  const s = await api.get('/api/bd/scan/status')
  if (!mounted) return
  scan.value = s
  if (scan.value.running) scanTimer = setTimeout(poll, 1500)
  else await reload()
}
async function toggleOwned(r) {
  await api.patch(`/api/bd/releases/${r.id}`, { owned: !r.owned }); await reload()
}
async function bind(r, e) {
  const v = e.target.value
  await api.patch(`/api/bd/releases/${r.id}`, { bangumi_id: v ? Number(v) : null }); await reload()
}
async function del(r) {
  if (!confirm(`移除 BD 记录「${r.title}」?(只删库记录,不动磁盘文件)`)) return
  await api.delete(`/api/bd/releases/${r.id}`); await reload()
}
function toggle(id) {
  expanded.value.has(id) ? expanded.value.delete(id) : expanded.value.add(id)
  expanded.value = new Set(expanded.value)
}
const SRC = { bdrip: ['BDRip', 'accent'], raw_disc: ['自购原盘', 'green'] }
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 18px; flex-wrap: wrap;">
      <div class="page-title" style="margin: 0;">BD 收藏</div>
      <div class="spacer" />
      <div class="filters">
        <button v-for="f in [['all','全部'],['owned','已购买'],['unowned','未购买'],['unbound','未绑定']]"
                :key="f[0]" class="btn sm" :class="{ primary: filter === f[0] }" @click="filter = f[0]">
          {{ f[1] }}
        </button>
      </div>
      <button class="btn sm" :disabled="scan?.running" @click="startScan"
              title="扫描下载根里的 BDRip 合集 + 已购原盘目录(/bd-owned)">
        <Icon name="scan" :size="14" /> {{ scan?.running ? '扫描中…' : '扫描 BD' }}
      </button>
    </div>

    <div v-if="scan" class="scan-bar card">
      <template v-if="scan.error"><span style="color: var(--red);">扫描失败:{{ scan.error }}</span></template>
      <template v-else>
        <Icon name="disc" :size="14" style="color: var(--accent);" />
        <strong>{{ scan.running ? '扫描中' : '扫描完成' }}</strong>
        <span class="muted" v-if="scan.current">· {{ scan.current }}</span>
        <span>· 发行 {{ scan.releases }} 套 · 特典 {{ scan.extras }} 项</span>
        <div class="spacer" />
        <button v-if="!scan.running" class="btn sm" @click="scan = null"><Icon name="close" :size="13" /></button>
      </template>
    </div>

    <p class="muted" style="font-size: 12.5px; margin-bottom: 12px;">
      共 {{ counts.all }} 套 · 已购买 {{ counts.owned }} · 未绑定 {{ counts.unbound }}。
      「已购买」会让对应番剧<b>完全不自动下载</b>(收藏即可)。
    </p>

    <div v-if="!shown.length" class="empty card">还没有 BD 记录 — 点「扫描 BD」识别。</div>

    <div v-for="r in shown" :key="r.id" class="bd-row card">
      <div class="row" style="flex-wrap: wrap; gap: 8px;">
        <img v-if="r.poster" :src="r.poster" class="bd-poster" />
        <div class="bd-meta">
          <div class="row" style="gap: 8px; flex-wrap: wrap;">
            <strong class="bd-title" :title="r.title">{{ r.title }}</strong>
            <span class="tag" :class="SRC[r.source_kind]?.[1]">{{ SRC[r.source_kind]?.[0] || r.source_kind }}</span>
            <span v-if="r.extra_count" class="tag">特典 {{ r.extra_count }}</span>
          </div>
          <div class="row" style="gap: 8px; margin-top: 8px; flex-wrap: wrap;">
            <select class="input sm" :value="r.bangumi_id ?? ''" @change="bind(r, $event)" style="max-width: 240px;">
              <option value="">未绑定番剧</option>
              <option v-for="b in bangumiList" :key="b.id" :value="b.id">{{ b.title }}</option>
            </select>
            <button class="btn sm" :class="r.owned ? 'primary' : ''" @click="toggleOwned(r)">
              <Icon :name="r.owned ? 'check' : 'plus'" :size="13" /> {{ r.owned ? '已购买' : '标为已购买' }}
            </button>
            <button v-if="r.extra_count" class="btn sm" @click="toggle(r.id)">
              <Icon :name="expanded.has(r.id) ? 'chevron-down' : 'chevron-right'" :size="13" /> 特典
            </button>
            <button class="btn sm danger" @click="del(r)"><Icon name="trash" :size="13" /> 移除</button>
          </div>
        </div>
      </div>
      <div v-if="expanded.has(r.id)" style="margin-top: 12px;">
        <BdReleases :releases="[r]" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; gap: 6px; }
.scan-bar { display: flex; align-items: center; gap: 8px; padding: 9px 16px; margin-bottom: 12px;
  font-size: 12.5px; border-color: var(--accent-dim); flex-wrap: wrap; }
.bd-row { margin-bottom: 10px; padding: 13px 16px; }
.bd-poster { width: 46px; aspect-ratio: 5/7; object-fit: cover; border-radius: 5px; border: 1px solid var(--border); }
.bd-meta { flex: 1; min-width: 0; }
.bd-title { word-break: break-all; }
.input.sm { padding: 4px 8px; font-size: 12.5px; }
.empty { text-align: center; padding: 50px; color: var(--text-dim); }
</style>
