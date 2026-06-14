<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, fmtSize } from '../api'
import Icon from './Icon.vue'
import Chips from './Chips.vue'

const props = defineProps({
  bangumiId: { type: Number, required: true },     // mikan_bangumi_id
  subgroupId: { type: String, required: true },
  include: { type: String, default: '' },
  exclude: { type: String, default: '' },
  excludeBatch: { type: [Boolean, null], default: null },
  // 手动勾选偏差:{ guid: true(强制下) / false(强制排除) },与规则结果一致时自动清除
  overrides: { type: Object, default: () => ({}) },
  epsTotal: { type: Number, default: 0 },          // 已知总集数时显示完整度
})
const emit = defineEmits(['stats', 'update:overrides'])

const items = ref([])
const loading = ref(false)
const health = ref({})        // torrent_url → {seeders, leechers} | {error}
const healthLoading = ref(false)
let timer = null

function isChecked(it) {
  return props.overrides[it.guid] ?? it.pass
}

function toggle(it) {
  setOverride(it, !isChecked(it))
  emitAll()
}

function setOverride(it, target) {
  if (target === it.pass) delete props.overrides[it.guid]
  else props.overrides[it.guid] = target
}

function bulk(mode) {
  // mode: 'all' 全选 | 'none' 清空 | 'rule' 恢复规则默认
  for (const it of items.value) {
    if (mode === 'rule') delete props.overrides[it.guid]
    else setOverride(it, mode === 'all')
  }
  emitAll()
}

function emitAll() {
  emit('update:overrides', { ...props.overrides })
  emit('stats', { pass: items.value.filter(isChecked).length, total: items.value.length })
}

// 完整度:勾选条目覆盖的集数集合
const coverage = computed(() => {
  const eps = new Set()
  for (const it of items.value.filter(isChecked))
    for (const e of it.episodes || [])
      if (typeof e === 'number') eps.add(e)
  const covered = [...eps].sort((a, b) => a - b)
  let missing = []
  if (props.epsTotal > 0) {
    for (let i = 1; i <= props.epsTotal; i++) if (!eps.has(i)) missing.push(i)
  }
  return { covered, missing }
})

function fmtRange(nums) {
  if (!nums.length) return '无'
  const parts = []
  let s = nums[0], p = nums[0]
  for (const n of nums.slice(1).concat([NaN])) {
    if (n === p + 1) { p = n; continue }
    parts.push(s === p ? `${s}` : `${s}-${p}`)
    s = p = n
  }
  return parts.join(', ')
}

async function load() {
  loading.value = true
  try {
    const p = new URLSearchParams({
      bangumi_id: props.bangumiId, subgroup_id: props.subgroupId,
      include: props.include, exclude: props.exclude,
      exclude_batch: props.excludeBatch === null ? false : props.excludeBatch,
    })
    items.value = await api.get(`/api/search/preview?${p}`)
    emitAll()
  } catch { /* 预览失败不阻塞表单 */ }
  loading.value = false
}

async function checkHealth() {
  healthLoading.value = true
  try {
    const urls = items.value.map(i => i.torrent_url).filter(Boolean).slice(0, 40)
    health.value = await api.post('/api/search/scrape', { torrent_urls: urls })
  } catch { /* 忽略 */ }
  healthLoading.value = false
}

watch(() => [props.include, props.exclude, props.excludeBatch], () => {
  clearTimeout(timer)
  timer = setTimeout(load, 450)
})
onMounted(load)
</script>

<template>
  <div>
    <div class="row" style="margin: 14px 0 8px; flex-wrap: wrap;">
      <strong style="font-size: 13px;">勾选要下载的源</strong>
      <span class="muted" style="font-size: 12px;">
        {{ loading ? '计算中…' : `已选 ${items.filter(isChecked).length} / ${items.length} 条` }}
      </span>
      <div class="spacer" />
      <button class="btn sm" @click="bulk('all')">全选</button>
      <button class="btn sm" @click="bulk('none')">清空</button>
      <button class="btn sm" @click="bulk('rule')">按规则</button>
      <button class="btn sm" :disabled="healthLoading" @click="checkHealth">
        <Icon name="zap" :size="13" /> {{ healthLoading ? '探测中…' : '检测活跃度' }}
      </button>
    </div>

    <div v-if="epsTotal > 0 && !loading" class="coverage muted">
      完整度:覆盖 <b style="color: var(--green)">{{ fmtRange(coverage.covered) }}</b>
      <template v-if="coverage.missing.length">
        ,缺 <b style="color: var(--red)">{{ fmtRange(coverage.missing) }}</b>(共缺 {{ coverage.missing.length }} 集)
      </template>
      <template v-else-if="coverage.covered.length">,全 {{ epsTotal }} 集齐 ✓</template>
    </div>

    <p v-if="!loading && items.length && !items.some(isChecked)" class="warn">
      <Icon name="alert" :size="14" /> 当前没有任何源被选中 — 不会下载任何内容
    </p>
    <div class="preview-list">
      <label v-for="p in items" :key="p.guid" class="preview-item"
             :class="{ off: !isChecked(p) }" :title="p.title">
        <input type="checkbox" :checked="isChecked(p)" @change="toggle(p)" />
        <Chips v-if="p.chips" :chips="p.chips" />
        <span v-else class="p-title">{{ p.title }}</span>
        <span v-if="overrides[p.guid] !== undefined" class="tag blue">手动</span>
        <span v-if="health[p.torrent_url]" class="tag"
              :class="(health[p.torrent_url].seeders ?? 0) > 0 ? 'green' : 'red'">
          {{ health[p.torrent_url].error ? '探测失败'
             : `${health[p.torrent_url].seeders} 做种 / ${health[p.torrent_url].leechers} 下载` }}
        </span>
        <div class="spacer" />
        <span class="muted reason">
          {{ isChecked(p) ? fmtSize(p.size) : (p.reason || '手动排除') }}
        </span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.warn {
  color: var(--accent); font-size: 12.5px; margin-bottom: 8px;
  background: #2a2113; border: 1px solid var(--accent-dim);
  border-radius: 8px; padding: 7px 12px;
}
.coverage { font-size: 12.5px; margin-bottom: 8px; }
.preview-list {
  max-height: 36vh; overflow-y: auto; border: 1px solid var(--border);
  border-radius: 8px; padding: 4px;
}
.preview-item {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border-radius: 6px; font-size: 12.5px; cursor: pointer; flex-wrap: wrap;
}
.preview-item:hover { background: var(--bg-hover); }
.preview-item.off { opacity: .5; }
.preview-item input { accent-color: var(--accent); flex-shrink: 0; }
.p-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 46%; }
.reason { font-size: 11.5px; flex-shrink: 0; }
</style>
