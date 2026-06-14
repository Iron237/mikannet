<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, fmtSize } from '../api'
import SubscribeWizard from '../components/SubscribeWizard.vue'
import Icon from '../components/Icon.vue'

const SOURCES = [
  { id: 'mikan', label: 'mikan', hint: '蜜柑计划(按番剧聚合)' },
  { id: 'nyaa', label: 'nyaa', hint: 'nyaa.si(全球)' },
  { id: 'dmhy', label: 'dmhy', hint: '动漫花园' },
]

const source = ref('mikan')
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const series = ref([])              // mikan 番剧命中(选海报)
const currentSeries = ref(null)     // 已选番剧(含封面)
const torrents = ref([])
const showRaw = ref(false)
const wizardPreset = ref(null)
const sourceOpen = ref(false)

// 分面筛选
const active = ref({ group: new Set(), resolution: new Set(), lang: new Set() })

const facets = computed(() => {
  const g = new Map(), r = new Map(), l = new Map()
  const bump = (m, k) => { if (k) m.set(k, (m.get(k) || 0) + 1) }
  for (const t of torrents.value) {
    bump(g, t.chips.group)
    bump(r, t.chips.resolution)
    for (const tag of t.chips.subtitle_tags || []) bump(l, tag)
  }
  const sort = m => [...m.entries()].sort((a, b) => b[1] - a[1])
  return { group: sort(g), resolution: sort(r), lang: sort(l) }
})

const filtered = computed(() => {
  const a = active.value
  return torrents.value.filter(t => {
    if (a.group.size && !a.group.has(t.chips.group)) return false
    if (a.resolution.size && !a.resolution.has(t.chips.resolution)) return false
    if (a.lang.size && !(t.chips.subtitle_tags || []).some(x => a.lang.has(x))) return false
    return true
  })
})

function toggleFacet(cat, val) {
  const s = active.value[cat]
  s.has(val) ? s.delete(val) : s.add(val)
  active.value = { ...active.value }
}
function clearFacets() {
  active.value = { group: new Set(), resolution: new Set(), lang: new Set() }
}

function resetResults() {
  series.value = []; currentSeries.value = null; torrents.value = []; clearFacets()
}

function pickSource(id) {
  source.value = id; sourceOpen.value = false; resetResults()
}

async function search() {
  if (!keyword.value.trim()) return
  loading.value = true; error.value = ''; resetResults()
  try {
    const data = await api.get(
      `/api/search/multi?source=${source.value}&keyword=${encodeURIComponent(keyword.value)}`)
    series.value = data.series || []
    torrents.value = data.torrents || []
  } catch (e) { error.value = e.message }
  loading.value = false
}

async function pickSeries(s) {
  loading.value = true; error.value = ''
  try {
    const data = await api.get(`/api/search/multi?source=mikan&bangumi_id=${s.mikan_bangumi_id}`)
    currentSeries.value = data.current_series
    torrents.value = data.torrents || []
    clearFacets()
  } catch (e) { error.value = e.message }
  loading.value = false
}

function backToSeries() { currentSeries.value = null; torrents.value = []; clearFacets() }

async function copyLink(t) {
  const link = t.magnet || t.torrent_url || t.page_url
  if (link) { try { await navigator.clipboard.writeText(link) } catch { /* ignore */ } }
}

function closeMenu() { sourceOpen.value = false }
onMounted(() => document.addEventListener('click', closeMenu))
onUnmounted(() => document.removeEventListener('click', closeMenu))
</script>

<template>
  <div class="page">
    <div class="page-title"><Icon name="search" :size="19" /> 搜索</div>

    <!-- 搜索栏 + 源选择 -->
    <div class="search-bar">
      <input v-model="keyword" class="input" placeholder="输入番剧名 / 字幕组关键词…"
             @keyup.enter="search" autofocus />
      <div class="src-select" @click.stop="sourceOpen = !sourceOpen">
        <span>{{ source }}</span><span class="caret">▾</span>
        <div v-if="sourceOpen" class="src-menu" @click.stop>
          <div v-for="s in SOURCES" :key="s.id" class="src-opt"
               :class="{ on: s.id === source }" @click="pickSource(s.id)">
            <span>{{ s.label }}</span><span class="muted hint">{{ s.hint }}</span>
          </div>
        </div>
      </div>
      <button class="btn primary" :disabled="loading" @click="search">
        {{ loading ? '搜索中…' : '搜索' }}
      </button>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <!-- mikan 番剧选择(海报) -->
    <div v-if="series.length && !currentSeries" class="series-grid">
      <div v-for="s in series" :key="s.mikan_bangumi_id" class="series-card" @click="pickSeries(s)">
        <img v-if="s.cover" :src="s.cover" loading="lazy" />
        <div v-else class="no-cover">无封面</div>
        <div class="series-title">{{ s.title }}</div>
      </div>
    </div>

    <!-- 结果区:左海报(mikan)+ 右 chips 墙 -->
    <div v-if="torrents.length" class="results">
      <aside v-if="currentSeries" class="poster-col">
        <img v-if="currentSeries.cover" :src="currentSeries.cover" />
        <div class="poster-title">{{ currentSeries.title }}</div>
        <button class="btn primary" style="width: 100%;"
                @click="wizardPreset = { mikan_bangumi_id: currentSeries.mikan_bangumi_id, title: currentSeries.title }">
          <Icon name="plus" :size="14" /> 订阅此番剧
        </button>
        <button class="btn" style="width: 100%;" @click="backToSeries"><Icon name="arrow-left" :size="14" /> 重选番剧</button>
      </aside>

      <div class="list-col">
        <!-- 分面筛选 -->
        <div v-if="facets.group.length || facets.resolution.length || facets.lang.length" class="facets">
          <template v-for="cat in ['group', 'resolution', 'lang']" :key="cat">
            <button v-for="[val] in facets[cat]" :key="cat + val" class="facet"
                    :class="['c-' + cat, { on: active[cat].has(val) }]"
                    @click="toggleFacet(cat, val)">{{ val }}</button>
          </template>
        </div>

        <div class="toolbar">
          <span class="muted">{{ filtered.length }} / {{ torrents.length }} 个结果</span>
          <div class="spacer" />
          <button class="btn sm" @click="showRaw = !showRaw">
            {{ showRaw ? '隐藏原名' : '显示原名' }}
          </button>
          <button v-if="active.group.size || active.resolution.size || active.lang.size"
                  class="btn sm" @click="clearFacets">清空筛选</button>
        </div>

        <div class="t-list">
          <div v-for="(t, i) in filtered" :key="i" class="t-row" :title="t.title">
            <div class="chips">
              <span v-if="t.chips.episode" class="ep">{{ t.chips.episode }}</span>
              <span v-if="t.chips.group" class="pill group">{{ t.chips.group }}</span>
              <span v-if="t.chips.resolution" class="pill res">{{ t.chips.resolution }}</span>
              <span v-for="tag in t.chips.subtitle_tags" :key="tag" class="pill lang">{{ tag }}</span>
              <span v-if="t.chips.version > 1" class="pill v">v{{ t.chips.version }}</span>
              <div class="spacer" />
              <span v-if="t.seeders != null" class="meta seed" title="做种 / 下载">
                ▲{{ t.seeders }} ▼{{ t.leechers }}
              </span>
              <span class="meta">{{ fmtSize(t.size) }}</span>
              <button class="icon-btn" title="复制链接" @click="copyLink(t)"><Icon name="copy" :size="14" /></button>
              <a v-if="t.page_url" class="icon-btn" :href="t.page_url" target="_blank"
                 rel="noopener" title="打开详情页"><Icon name="link" :size="14" /></a>
            </div>
            <div v-if="showRaw" class="raw">{{ t.title }}</div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!loading && !series.length && !torrents.length && keyword" class="empty muted">
      没有结果 — 换个关键词或来源试试
    </div>
    <div v-if="!keyword && !torrents.length && !series.length" class="empty muted">
      选择来源(mikan / nyaa / dmhy),输入关键词开始搜索
    </div>

    <SubscribeWizard v-if="wizardPreset" :preset="wizardPreset" @close="wizardPreset = null" />
  </div>
</template>

<style scoped>
.search-bar { display: flex; gap: 10px; margin-bottom: 18px; align-items: stretch; }
.search-bar .input { flex: 1; }

.src-select {
  position: relative; min-width: 116px; padding: 8px 12px; cursor: pointer;
  background: #0b0e14; border: 1px solid var(--border); border-radius: 8px;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  user-select: none; color: var(--accent); font-weight: 600;
}
.caret { color: var(--text-dim); font-size: 11px; }
.src-menu {
  position: absolute; top: calc(100% + 6px); right: 0; min-width: 200px; z-index: 30;
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px;
  padding: 6px; box-shadow: 0 12px 32px rgba(0,0,0,.5);
}
.src-opt {
  display: flex; flex-direction: column; gap: 1px; padding: 8px 10px; border-radius: 7px;
  color: var(--text); font-weight: 600;
}
.src-opt:hover { background: var(--bg-hover); }
.src-opt.on { color: var(--accent); }
.src-opt .hint { font-size: 11px; font-weight: 400; }

.err { color: var(--red); margin-bottom: 12px; }
.empty { text-align: center; padding: 60px 0; }

/* mikan 海报选择 */
.series-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 14px;
}
.series-card {
  cursor: pointer; border-radius: 10px; overflow: hidden;
  border: 1px solid var(--border); background: #0b0e14; transition: all .15s;
}
.series-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.series-card img { width: 100%; aspect-ratio: 3/4; object-fit: cover; display: block; }
.no-cover { aspect-ratio: 3/4; display: flex; align-items: center; justify-content: center; color: var(--text-dim); font-size: 12px; }
.series-title { padding: 7px 9px; font-size: 12.5px; line-height: 1.35; }

/* 结果区 */
.results { display: flex; gap: 20px; align-items: flex-start; }
.poster-col {
  flex-shrink: 0; width: 180px; position: sticky; top: 18px;
  display: flex; flex-direction: column; gap: 8px;
}
.poster-col img { width: 100%; border-radius: 10px; border: 1px solid var(--border); display: block; }
.poster-title { font-weight: 600; font-size: 13.5px; line-height: 1.4; }
.list-col { flex: 1; min-width: 0; }

/* 分面 */
.facets {
  display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 14px;
  padding-bottom: 14px; border-bottom: 1px solid var(--border);
}
.facet {
  padding: 3px 11px; border-radius: 20px; font-size: 12px; cursor: pointer;
  border: 1px solid var(--border); background: var(--bg-card); color: var(--text-dim);
  transition: all .12s;
}
.facet:hover { border-color: var(--accent-dim); color: var(--text); }
.facet.c-group { color: var(--accent); border-color: var(--accent-dim); }
.facet.c-lang { color: var(--blue); border-color: #2f4b66; }
.facet.on { background: var(--accent); border-color: var(--accent); color: #1a1207; font-weight: 700; }
.facet.c-lang.on { background: var(--blue); border-color: var(--blue); color: #07121d; }

.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }

/* chips 行 */
.t-list { display: flex; flex-direction: column; gap: 7px; }
.t-row {
  border: 1px solid var(--border); border-radius: 10px; padding: 9px 12px;
  background: var(--bg-card); transition: border-color .12s;
}
.t-row:hover { border-color: var(--accent-dim); }
.chips { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.ep {
  min-width: 30px; height: 22px; padding: 0 7px; border-radius: 6px;
  display: inline-flex; align-items: center; justify-content: center;
  background: #143021; color: var(--green); font-weight: 700; font-size: 12px;
  border: 1px solid #1f5236; flex-shrink: 0;
}
.pill {
  padding: 2px 10px; border-radius: 20px; font-size: 12px;
  border: 1px solid var(--border); color: var(--text-dim); white-space: nowrap;
}
.pill.group { color: var(--accent); border-color: var(--accent-dim); font-weight: 600; }
.pill.res { color: var(--text); }
.pill.lang { color: var(--blue); border-color: #2f4b66; }
.pill.v { color: var(--accent); border-color: var(--accent); font-weight: 700; }
.meta { font-size: 12px; color: var(--text-dim); white-space: nowrap; }
.meta.seed { color: var(--green); }
.icon-btn {
  border: none; background: transparent; cursor: pointer; font-size: 13px;
  padding: 2px 4px; border-radius: 6px; color: var(--text-dim); line-height: 1;
}
.icon-btn:hover { background: var(--bg-hover); color: var(--text); }
.raw {
  margin-top: 7px; font-size: 11.5px; color: var(--text-dim); word-break: break-all;
  border-top: 1px dashed var(--border); padding-top: 6px;
}

@media (max-width: 768px) {
  .search-bar { flex-wrap: wrap; }
  .search-bar .input { flex: 1 1 100%; }
  .results { flex-direction: column; }
  .poster-col { position: static; width: 100%; flex-direction: row; }
  .poster-col img { width: 96px; }
}
</style>
