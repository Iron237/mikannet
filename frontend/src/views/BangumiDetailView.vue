<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtSize } from '../api'
import SubscribeWizard from '../components/SubscribeWizard.vue'

const route = useRoute()
const router = useRouter()
const b = ref(null)
const expanded = ref(new Set())
const showWizard = ref(false)
const confirmRemove = ref(false)
const removeFiles = ref(false)
const removing = ref(false)

async function doRemove() {
  removing.value = true
  await api.delete(`/api/bangumi/${b.value.id}?delete_files=${removeFiles.value}`)
  router.push('/')
}

const epStatus = {
  missing: ['未下载', ''], pending: ['等待中', 'blue'], downloading: ['下载中', 'accent'],
  completed: ['已完成', 'green'], archived: ['已入库', 'green'],
  download_error: ['错误', 'red'], submit_failed: ['失败', 'red'],
}

async function load() {
  b.value = await api.get(`/api/bangumi/${route.params.id}`)
}

function toggleExpand(id) {
  expanded.value.has(id) ? expanded.value.delete(id) : expanded.value.add(id)
  expanded.value = new Set(expanded.value)
}

async function redownload(ep) {
  if (!ep.torrent_id) return
  await api.post(`/api/tasks/${ep.torrent_id}/resume`)
  await load()
}

onMounted(load)
</script>

<template>
  <div v-if="b">
    <div class="hero" :style="b.backdrop ? { backgroundImage: `url(${b.backdrop})` } : {}">
      <div class="hero-inner page">
        <img v-if="b.poster" :src="b.poster" class="hero-poster" />
        <div class="hero-info">
          <h1>{{ b.title }}</h1>
          <div class="muted" v-if="b.title_original">{{ b.title_original }}</div>
          <div class="row" style="margin-top: 10px; flex-wrap: wrap;">
            <span v-if="b.season" class="tag">{{ b.season }}</span>
            <span v-if="b.studio" class="tag">{{ b.studio }}</span>
            <span v-if="b.eps_total" class="tag">全 {{ b.eps_total }} 话</span>
            <span class="tag" :class="b.airing_status === 'airing' ? 'green' : ''">
              {{ b.airing_status === 'airing' ? '连载中' : '已完结' }}
            </span>
            <span v-if="b.score" class="tag accent">★ {{ b.score }}</span>
          </div>
          <p class="summary muted" v-if="b.summary">{{ b.summary }}</p>
          <div class="row" style="margin-top: 10px; flex-wrap: wrap;">
            <span class="muted" style="font-size: 12px;" v-for="s in b.subscriptions" :key="s.id">
              📡 {{ s.subgroup_name }}{{ s.enabled ? '' : '(已停用)' }}
            </span>
          </div>
          <div class="row" style="margin-top: 14px;">
            <button class="btn primary" @click="showWizard = true">＋ 添加订阅</button>
            <button class="btn danger" @click="confirmRemove = true">🗑 移除番剧</button>
          </div>
        </div>
      </div>
    </div>

    <div class="page">
      <div class="page-title">剧集</div>
      <div v-if="!b.episodes.length" class="muted">还没有剧集记录</div>
      <div v-for="ep in b.episodes" :key="ep.id ?? 'missing-' + ep.number" class="card ep-row"
           :class="{ missing: ep.status === 'missing' }">
        <div class="row" style="cursor: pointer;" @click="ep.id && toggleExpand(ep.id)">
          <strong class="ep-num">第 {{ ep.number ?? '?' }} 话</strong>
          <span class="tag" :class="epStatus[ep.status]?.[1]">{{ epStatus[ep.status]?.[0] ?? ep.status }}</span>
          <span v-if="ep.version > 1" class="tag accent">v{{ ep.version }}</span>
          <div class="spacer" />
          <span class="muted" style="font-size: 12px;" v-if="ep.files.length">
            {{ ep.files.length }} 个文件
          </span>
        </div>
        <div v-if="expanded.has(ep.id) && ep.files.length" class="files">
          <div v-for="f in ep.files" :key="f.id" class="file">
            <div class="muted path">{{ f.path }}</div>
            <div class="row" style="flex-wrap: wrap; gap: 6px; margin-top: 4px;">
              <span v-if="f.resolution" class="tag blue">{{ f.resolution }}</span>
              <span v-if="f.codec" class="tag">{{ f.codec }}</span>
              <span v-if="f.bitrate" class="tag">{{ (f.bitrate / 1e6).toFixed(1) }} Mbps</span>
              <span class="tag">{{ fmtSize(f.size) }}</span>
              <span v-for="(a, i) in f.audio_tracks" :key="'a' + i" class="tag">🔊 {{ a.lang || a.codec }}</span>
              <span v-for="(s, i) in f.subtitle_tracks" :key="'s' + i" class="tag green">💬 {{ s.lang || s.title || s.codec }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <SubscribeWizard v-if="showWizard"
                     :preset="{ mikan_bangumi_id: b.mikan_bangumi_id, title: b.title }"
                     @close="showWizard = false; load()" />

    <div v-if="confirmRemove" class="modal-mask" @click.self="confirmRemove = false">
      <div class="modal" style="width: 440px;">
        <h3 style="margin-bottom: 10px;">移除番剧</h3>
        <p class="muted">将删除「{{ b.title }}」及其全部订阅、剧集记录和下载任务(qB 中一并移除)。</p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="removeFiles" />
          同时删除 NAS 上已下载的文件(不可恢复)
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="confirmRemove = false">取消</button>
          <button class="btn danger" :disabled="removing" @click="doRemove">
            {{ removing ? '移除中…' : '确认移除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hero {
  background-size: cover; background-position: center top; position: relative;
}
.hero::before {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(14,17,23,.55), var(--bg) 96%);
}
.hero-inner { position: relative; display: flex; gap: 26px; padding-top: 44px; padding-bottom: 26px; }
.hero-poster {
  width: 190px; aspect-ratio: 5/7; object-fit: cover; border-radius: 12px;
  border: 1px solid var(--border); box-shadow: 0 10px 30px rgba(0,0,0,.5);
}
.hero-info h1 { font-size: 26px; }
.summary {
  margin-top: 12px; font-size: 13px; max-width: 720px;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
}
.ep-row { margin-bottom: 8px; padding: 12px 16px; }
.ep-row.missing { opacity: .55; border-style: dashed; }
.ep-num { width: 90px; }

@media (max-width: 768px) {
  .hero-inner { flex-direction: column; align-items: center; text-align: center;
                padding-top: 24px; gap: 14px; }
  .hero-poster { width: 140px; }
  .hero-info h1 { font-size: 20px; }
  .hero-info .row { justify-content: center; }
  .summary { font-size: 12.5px; -webkit-line-clamp: 3; }
  .ep-num { width: 70px; font-size: 13px; }
}
.files { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px; }
.file { margin-bottom: 8px; }
.path { font-size: 12px; word-break: break-all; }
</style>
