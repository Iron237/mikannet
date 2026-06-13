<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import RulePreview from './RulePreview.vue'

const props = defineProps({
  // 从番剧详情页打开时预设,直接跳到选字幕组
  preset: { type: Object, default: null },   // { mikan_bangumi_id, title }
})
const emit = defineEmits(['close'])

const step = ref(1)
const keyword = ref('')
const searching = ref(false)
const results = ref([])
const picked = ref(null)
const detail = ref(null)
const group = ref(null)
const submitting = ref(false)
const error = ref('')
const previewStats = ref({ pass: -1, total: 0 })
const overrides = ref({})   // guid → true(强制下)/false(强制排除)

const form = ref({
  include_keywords: '',
  exclude_keywords: '',
  exclude_batch: null,
  backfill: true,
})

async function search() {
  if (!keyword.value.trim()) return
  searching.value = true
  error.value = ''
  try {
    results.value = await api.get(`/api/search?keyword=${encodeURIComponent(keyword.value)}`)
  } catch (e) { error.value = e.message }
  searching.value = false
}

async function pick(r) {
  picked.value = r
  step.value = 2
  detail.value = null
  try {
    detail.value = await api.get(`/api/search/bangumi/${r.mikan_bangumi_id}`)
  } catch (e) { error.value = e.message }
}

function pickGroup(g) {
  group.value = g
  step.value = 3
}

async function submit() {
  submitting.value = true
  error.value = ''
  try {
    await api.post('/api/subscriptions', {
      mikan_bangumi_id: picked.value.mikan_bangumi_id,
      mikan_subgroup_id: group.value.subgroup_id,
      subgroup_name: group.value.name,
      include_keywords: form.value.include_keywords.split(/[,,\s]+/).filter(Boolean),
      exclude_keywords: form.value.exclude_keywords.split(/[,,\s]+/).filter(Boolean),
      pinned_guids: Object.keys(overrides.value).filter(g => overrides.value[g] === true),
      blocked_guids: Object.keys(overrides.value).filter(g => overrides.value[g] === false),
      exclude_batch: form.value.exclude_batch,
      backfill: form.value.backfill,
    })
    emit('close')
  } catch (e) {
    error.value = e.message
    submitting.value = false
  }
}

onMounted(() => {
  if (props.preset) pick(props.preset)
})
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" style="width: min(840px, 94vw);">
      <div class="row" style="margin-bottom: 16px;">
        <h3>添加订阅</h3>
        <span class="muted">— 第 {{ step }} / 3 步</span>
        <div class="spacer" />
        <button class="btn sm" @click="emit('close')">✕</button>
      </div>

      <p v-if="error" style="color: var(--red); margin-bottom: 12px;">{{ error }}</p>

      <!-- 步骤 1:搜索 -->
      <template v-if="step === 1">
        <div class="row" style="margin-bottom: 14px;">
          <input v-model="keyword" class="input" placeholder="搜索蜜柑番剧,如:药屋少女"
                 @keyup.enter="search" autofocus />
          <button class="btn primary" :disabled="searching" @click="search">
            {{ searching ? '搜索中…' : '搜索' }}
          </button>
        </div>
        <div class="search-grid">
          <div v-for="r in results" :key="r.mikan_bangumi_id" class="search-item" @click="pick(r)">
            <img v-if="r.cover" :src="r.cover" loading="lazy" />
            <div v-else class="no-cover">无封面</div>
            <div class="search-title">{{ r.title }}</div>
          </div>
        </div>
      </template>

      <!-- 步骤 2:选字幕组 -->
      <template v-if="step === 2">
        <div class="row" style="margin-bottom: 12px;">
          <button v-if="!props.preset" class="btn sm" @click="step = 1">← 返回</button>
          <strong>{{ picked.title }}</strong>
        </div>
        <div v-if="!detail" class="muted">加载字幕组…</div>
        <div v-else class="group-list">
          <div v-for="g in detail.subgroups" :key="g.subgroup_id" class="group-item"
               @click="pickGroup(g)">
            <div class="row">
              <strong>{{ g.name }}</strong>
              <span class="tag">{{ g.torrent_count }} 个种子</span>
            </div>
            <div class="muted recent" v-for="t in g.recent_titles.slice(0, 2)" :key="t">{{ t }}</div>
          </div>
        </div>
      </template>

      <!-- 步骤 3:规则 + 全部源实时预览 -->
      <template v-if="step === 3">
        <div class="row" style="margin-bottom: 12px;">
          <button class="btn sm" @click="step = 2">← 返回</button>
          <strong>{{ picked.title }}</strong>
          <span class="tag accent">{{ group.name }}</span>
          <div class="spacer" />
          <button class="btn primary" :disabled="submitting || previewStats.pass === 0"
                  :title="previewStats.pass === 0 ? '当前规则不会下载任何内容' : ''"
                  @click="submit">
            {{ submitting ? '创建中…' : '创建订阅' }}
          </button>
        </div>
        <div class="form-grid">
          <label>包含关键词(全部满足,空格分隔)
            <input v-model="form.include_keywords" class="input" placeholder="如:1080 内封" />
          </label>
          <label>排除关键词(任一命中即排除)
            <input v-model="form.exclude_keywords" class="input" placeholder="如:720" />
          </label>
          <label>合集策略
            <select v-model="form.exclude_batch" class="input">
              <option :value="null">自动(连载排除合集,完结允许)</option>
              <option :value="true">总是排除合集</option>
              <option :value="false">总是允许合集</option>
            </select>
          </label>
          <label>历史剧集
            <select v-model="form.backfill" class="input">
              <option :value="true">补齐全部历史</option>
              <option :value="false">只追新剧集</option>
            </select>
          </label>
        </div>

        <RulePreview :bangumi-id="picked.mikan_bangumi_id" :subgroup-id="group.subgroup_id"
                     :include="form.include_keywords" :exclude="form.exclude_keywords"
                     :exclude-batch="form.exclude_batch"
                     :overrides="overrides"
                     @update:overrides="overrides = $event"
                     @stats="previewStats = $event" />
      </template>
    </div>
  </div>
</template>

<style scoped>
.search-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px; max-height: 50vh; overflow-y: auto;
}
.search-item {
  cursor: pointer; border-radius: 8px; overflow: hidden;
  border: 1px solid var(--border); background: #0b0e14; transition: all .15s;
}
.search-item:hover { border-color: var(--accent); }
.search-item img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
.no-cover {
  aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
  color: var(--text-dim); font-size: 12px;
}
.search-title { padding: 7px 9px; font-size: 12.5px; }
.group-list { max-height: 55vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.group-item {
  padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px;
  cursor: pointer; transition: all .15s;
}
.group-item:hover { border-color: var(--accent); background: var(--bg-hover); }
.recent {
  font-size: 11.5px; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; margin-top: 3px;
}
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-grid label { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
.preview-list {
  max-height: 38vh; overflow-y: auto; border: 1px solid var(--border);
  border-radius: 8px; padding: 4px;
}
.preview-item {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border-radius: 6px; font-size: 12.5px;
}
.preview-item:hover { background: var(--bg-hover); }
.preview-item.off { opacity: .45; }
.dot {
  width: 18px; height: 18px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; background: #3a2326; color: var(--red);
}
.dot.on { background: #1d3a26; color: var(--green); }
.p-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 55%; }
</style>
