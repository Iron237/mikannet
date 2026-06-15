<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'

const items = ref([])
const filter = ref('all')      // all | airing | finished
const srcFilter = ref('all')   // all | bd | bd_owned | bd_unowned | web
const keyword = ref('')
const loading = ref(true)
const scan = ref(null)       // 番剧库扫描状态
let scanTimer = null
const manageMode = ref(false)
const selected = ref(new Set())
const delConfirm = ref(null)
const delFiles = ref(false)
const busy = ref(false)
const autoConfirm = ref(null)   // 智能下载确认 { ids }
const autoEnable = ref(false)   // 同时设为常驻智能下载
const autoScan = ref(null)      // 智能扫描进度
let autoTimer = null

const SEASON_ORDER = { '秋': 4, '夏': 3, '春': 2, '冬': 1 }

const groups = computed(() => {
  let list = items.value
  if (filter.value !== 'all') list = list.filter(b => b.airing_status === filter.value)
  const sf = srcFilter.value
  if (sf === 'bd') list = list.filter(b => b.has_bd)
  else if (sf === 'bd_owned') list = list.filter(b => b.has_bd && b.bd_owned)
  else if (sf === 'bd_unowned') list = list.filter(b => b.has_bd && !b.bd_owned)
  else if (sf === 'web') list = list.filter(b => b.has_web)
  const kw = keyword.value.trim().toLowerCase()
  if (kw) list = list.filter(b =>
    b.title.toLowerCase().includes(kw) || (b.studio || '').toLowerCase().includes(kw))

  const map = new Map()
  for (const b of list) {
    const key = b.season || (b.year ? `${b.year}年` : '未知季度')
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(b)
  }
  return [...map.entries()]
    .map(([season, list]) => ({
      season, list,
      _sort: (list[0].year || 0) * 10 + (SEASON_ORDER[season.slice(-1)] || 0),
    }))
    .sort((a, b) => b._sort - a._sort)
})

const visibleIds = computed(() => groups.value.flatMap(g => g.list.map(b => b.id)))
const allSelected = computed(() =>
  visibleIds.value.length > 0 && visibleIds.value.every(id => selected.value.has(id)))

function isSel(id) { return selected.value.has(id) }
function toggleSel(id) {
  const s = new Set(selected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selected.value = s
}
function selectAll() { selected.value = new Set(visibleIds.value) }
function clearSel() { selected.value = new Set() }
function toggleManage() {
  manageMode.value = !manageMode.value
  if (!manageMode.value) clearSel()
}
function onCard(b, e) {
  if (!manageMode.value) return     // 正常模式:RouterLink 跳转
  e.preventDefault()
  toggleSel(b.id)
}

async function reload() {
  items.value = await api.get('/api/bangumi')
  const ids = new Set(items.value.map(b => b.id))
  selected.value = new Set([...selected.value].filter(id => ids.has(id)))
}

async function doDelete() {
  busy.value = true
  try {
    await api.post('/api/bangumi/batch-delete',
      { ids: delConfirm.value.ids, delete_files: delFiles.value })
    delConfirm.value = null; delFiles.value = false; clearSel()
    await reload()
  } finally { busy.value = false }
}

async function doAutoScan() {
  busy.value = true
  try {
    await api.post('/api/bangumi/auto-scan',
      { ids: autoConfirm.value.ids, enable_auto: autoEnable.value })
    autoConfirm.value = null; autoEnable.value = false; clearSel(); manageMode.value = false
    pollAutoScan()
  } catch (e) { autoScan.value = { error: e.message } }
  finally { busy.value = false }
}
async function pollAutoScan() {
  autoScan.value = await api.get('/api/bangumi/auto-scan/status')
  if (autoScan.value.running) { autoTimer = setTimeout(pollAutoScan, 1500) }
  else { await reload() }
}
const autoSubmitted = computed(() =>
  (autoScan.value?.result || []).reduce((n, r) => n + (r.submitted || 0), 0))

async function startScan() {
  try {
    await api.post('/api/import/library-scan', {})
    pollScan()
  } catch (e) { scan.value = { error: e.message } }
}
async function pollScan() {
  scan.value = await api.get('/api/import/library-scan/status')
  if (scan.value.running) {
    scanTimer = setTimeout(pollScan, 1500)
  } else {
    await reload()   // 扫描完成,刷新封面墙(集数角标会更新)
  }
}

onMounted(async () => {
  await reload()
  loading.value = false
  const s = await api.get('/api/import/library-scan/status')
  if (s.running) { scan.value = s; pollScan() }
  const a = await api.get('/api/bangumi/auto-scan/status')
  if (a.running) { autoScan.value = a; pollAutoScan() }
})
onUnmounted(() => { clearTimeout(scanTimer); clearTimeout(autoTimer) })
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 18px; flex-wrap: wrap;">
      <div class="page-title" style="margin: 0;">番剧库</div>
      <div class="spacer" />
      <div class="search-wrap">
        <Icon name="search" :size="15" class="search-ic" />
        <input v-model="keyword" class="input search-box" placeholder="搜索标题 / 制作公司" />
      </div>
      <div class="filters">
        <button v-for="f in [['all','全部'],['airing','连载中'],['finished','已完结']]" :key="f[0]"
                class="btn sm" :class="{ primary: filter === f[0] }" @click="filter = f[0]">
          {{ f[1] }}
        </button>
      </div>
      <div class="filters">
        <button v-for="f in [['all','全部源'],['bd','BD'],['bd_owned','BD·已购'],['bd_unowned','BD·未购'],['web','Web']]"
                :key="f[0]" class="btn sm" :class="{ primary: srcFilter === f[0] }" @click="srcFilter = f[0]">
          {{ f[1] }}
        </button>
      </div>
      <button class="btn sm" :disabled="scan?.running" @click="startScan"
              title="扫描下载根目录,把已摆好的视频就地识别进库(不移动文件)">
        <Icon name="scan" :size="14" /> {{ scan?.running ? '扫描中…' : '扫描番剧库' }}
      </button>
      <button class="btn sm" :class="{ primary: manageMode }" @click="toggleManage">
        <Icon :name="manageMode ? 'check' : 'edit'" :size="14" /> {{ manageMode ? '完成' : '管理' }}
      </button>
    </div>

    <div v-if="scan" class="scan-bar card">
      <template v-if="scan.error"><span style="color: var(--red);">扫描失败:{{ scan.error }}</span></template>
      <template v-else>
        <strong>{{ scan.running ? '扫描中' : '扫描完成' }}</strong>
        <span class="muted">{{ scan.done }}/{{ scan.total }}</span>
        <span class="muted" v-if="scan.current">· {{ scan.current }}</span>
        <span>· 新增 {{ scan.registered }} · 匹配 {{ scan.matched?.length || 0 }} 部</span>
        <span class="muted" v-if="scan.updated">· 更新 {{ scan.updated }}</span>
        <span class="muted" v-if="scan.removed">· 移除 {{ scan.removed }}</span>
        <span class="muted" v-if="scan.skipped">· 跳过 {{ scan.skipped }} 裸盘</span>
        <span class="muted" v-if="scan.unmatched?.length">· 未匹配 {{ scan.unmatched.length }}</span>
        <div class="spacer" />
        <button v-if="!scan.running" class="btn sm" @click="scan = null"><Icon name="close" :size="13" /></button>
      </template>
    </div>

    <div v-if="manageMode" class="batch-bar card">
      <button class="btn sm" @click="allSelected ? clearSel() : selectAll()">
        {{ allSelected ? '取消全选' : '全选' }}
      </button>
      <button class="btn sm" :disabled="!selected.size" @click="clearSel">清空选择</button>
      <span class="muted" style="font-size: 12.5px;">已选 {{ selected.size }} 部</span>
      <div class="spacer" />
      <button class="btn sm primary" :disabled="!selected.size || busy || autoScan?.running"
              @click="autoConfirm = { ids: [...selected] }"
              title="扫所有字幕组,按偏好(BD>Web/分辨率/简中)补全缺集并升级现有源">
        <Icon name="zap" :size="13" /> 智能下载
      </button>
      <button class="btn sm danger" :disabled="!selected.size || busy"
              @click="delConfirm = { ids: [...selected] }"><Icon name="trash" :size="13" /> 批量删除</button>
    </div>

    <div v-if="autoScan" class="scan-bar card">
      <template v-if="autoScan.error"><span style="color: var(--red);">智能下载失败:{{ autoScan.error }}</span></template>
      <template v-else>
        <Icon name="zap" :size="14" style="color: var(--accent);" />
        <strong>{{ autoScan.running ? '智能扫描中' : '智能扫描完成' }}</strong>
        <span class="muted">{{ autoScan.done }}/{{ autoScan.total }}</span>
        <span class="muted" v-if="autoScan.current">· {{ autoScan.current }}</span>
        <span v-if="!autoScan.running">· 共提交 {{ autoSubmitted }} 个种子</span>
        <div class="spacer" />
        <button v-if="!autoScan.running" class="btn sm" @click="autoScan = null"><Icon name="close" :size="13" /></button>
      </template>
    </div>

    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="!groups.length" class="empty card">
      {{ keyword ? '没有匹配的番剧' : '番剧库还是空的 — 去订阅管理添加第一部番剧吧' }}
    </div>

    <section v-for="g in groups" :key="g.season" class="season-group">
      <h3 class="season-title">{{ g.season }} <span class="muted">{{ g.list.length }} 部</span></h3>
      <div class="grid">
        <component :is="manageMode ? 'div' : 'RouterLink'" v-for="b in g.list" :key="b.id"
                   :to="manageMode ? undefined : `/bangumi/${b.id}`"
                   class="poster-card" :class="{ manage: manageMode, sel: isSel(b.id) }"
                   @click="onCard(b, $event)">
          <div class="poster">
            <img v-if="b.poster" :src="b.poster" loading="lazy" :alt="b.title" />
            <div v-else class="poster-fallback">{{ b.title.slice(0, 2) }}</div>
            <span v-if="b.kind === 'tv' && b.airing_status === 'airing'" class="airing-badge">连载中</span>
            <span v-if="b.auto_best" class="auto-badge" title="已开启智能下载(定期扫描升级)"><Icon name="zap" :size="11" /></span>
            <!-- TV 显示集进度;剧场版/OVA 不是集概念 → 显示形态分类(已入库/未入库) -->
            <div class="ep-badge" v-if="b.kind === 'tv' && b.eps_total">{{ b.eps_downloaded }}/{{ b.eps_total }}</div>
            <div class="ep-badge kind" v-else-if="b.kind && b.kind !== 'tv'" :class="{ dim: !b.has_resource }">
              {{ b.kind === 'movie' ? '剧场版' : 'OVA' }}{{ b.has_resource ? '' : '·未入库' }}
            </div>
            <div v-if="manageMode" class="sel-check" :class="{ on: isSel(b.id) }">
              <Icon v-if="isSel(b.id)" name="check" :size="14" />
            </div>
          </div>
          <div class="info">
            <div class="title" :title="b.title">{{ b.title }}</div>
            <div class="meta muted">
              <span v-if="b.score" class="score">★ {{ b.score }}</span>
              <span class="studio" v-if="b.studio" :title="b.studio">{{ b.studio }}</span>
            </div>
          </div>
        </component>
      </div>
    </section>

    <div v-if="autoConfirm" class="modal-mask" @click.self="autoConfirm = null">
      <div class="modal" style="width: 460px;">
        <h3 style="margin-bottom: 10px;">智能下载 {{ autoConfirm.ids.length }} 部番剧</h3>
        <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
          扫描每部番剧的<b>所有字幕组</b>种子,按偏好挑最佳源:<br>
          片源 <b>BD &gt; Web</b> · 严格匹配分辨率与字幕(默认 1080P / 简中,可在设置改)。<br>
          缺失的集会补全;已有 Web 而出现合格 BD 的会下 BD 升级(完成后自动顶替)。
        </p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="autoEnable" />
          同时设为常驻智能下载(之后定期自动扫描补全/升级)
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="autoConfirm = null">取消</button>
          <button class="btn primary" :disabled="busy" @click="doAutoScan">
            <Icon name="zap" :size="13" /> 开始
          </button>
        </div>
      </div>
    </div>

    <div v-if="delConfirm" class="modal-mask" @click.self="delConfirm = null">
      <div class="modal" style="width: 440px;">
        <h3 style="margin-bottom: 10px;">删除 {{ delConfirm.ids.length }} 部番剧</h3>
        <p class="muted" style="font-size: 12.5px;">
          将移除番剧及其订阅/剧集/任务记录(虚拟库视图)。
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
  </div>
</template>

<style scoped>
.search-wrap { position: relative; display: inline-flex; align-items: center; }
.search-ic { position: absolute; left: 10px; color: var(--text-dim); pointer-events: none; }
.search-box { width: 240px; padding-left: 32px; }
.season-group { margin-bottom: 26px; }
.season-title {
  font-size: 15px; margin-bottom: 12px; color: var(--accent);
  border-left: 3px solid var(--accent); padding-left: 10px;
}
.season-title .muted { font-size: 12px; font-weight: 400; margin-left: 6px; }
.grid {
  display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
}
.poster-card {
  border-radius: var(--radius); overflow: hidden; background: var(--bg-card);
  border: 1px solid var(--border); transition: all .2s;
}
.poster-card:hover { transform: translateY(-3px); border-color: var(--accent-dim); }
.poster-card.manage { cursor: pointer; }
.poster-card.sel { border-color: var(--accent); }
.poster-card.sel .poster { outline: 2px solid var(--accent); outline-offset: -2px; }
.sel-check {
  position: absolute; top: 8px; right: 8px; width: 22px; height: 22px; border-radius: 50%;
  border: 2px solid #fff; background: rgba(0,0,0,.45); display: flex;
  align-items: center; justify-content: center; font-size: 13px; font-weight: 700; color: #fff;
}
.sel-check.on { background: var(--accent); border-color: var(--accent); color: #1a1207; }
.batch-bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 16px; margin-bottom: 16px;
  position: sticky; top: 8px; z-index: 10; border-color: var(--accent-dim);
}
.scan-bar {
  display: flex; align-items: center; gap: 8px; padding: 9px 16px; margin-bottom: 16px;
  font-size: 12.5px; border-color: var(--accent-dim); flex-wrap: wrap;
}
.poster { position: relative; aspect-ratio: 5/7; background: #0b0e14; }
.poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
.poster-fallback {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 38px; color: var(--text-dim);
}
.airing-badge {
  position: absolute; top: 8px; left: 8px;
  background: var(--green); color: #06130a; font-size: 11px; font-weight: 700;
  padding: 1px 8px; border-radius: 10px;
}
.auto-badge {
  position: absolute; top: 8px; right: 8px; display: inline-flex;
  align-items: center; justify-content: center; width: 20px; height: 20px;
  background: var(--accent); color: #1a1207; border-radius: 50%;
}
.ep-badge {
  position: absolute; bottom: 0; right: 0;
  background: rgba(0,0,0,.75); font-size: 11px; padding: 2px 8px;
  border-top-left-radius: 8px; color: var(--accent);
}
.ep-badge.kind { color: var(--blue); font-weight: 700; }
.ep-badge.kind.dim { color: var(--text-dim); }
.info { padding: 10px 12px 12px; }
.title {
  font-weight: 600; font-size: 13.5px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.meta { display: flex; gap: 8px; font-size: 12px; margin-top: 2px; align-items: center; }
.score { color: var(--accent); flex-shrink: 0; }
.studio { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 11.5px; }
.empty { text-align: center; padding: 60px; color: var(--text-dim); }
.filters { display: flex; gap: 6px; }

@media (max-width: 768px) {
  .grid { grid-template-columns: repeat(auto-fill, minmax(108px, 1fr)); gap: 10px; }
  .search-wrap { width: 100%; order: 3; }
  .search-box { width: 100%; }
  .info { padding: 7px 9px 9px; }
  .title { font-size: 12.5px; }
  .studio { display: none; }
}
</style>
