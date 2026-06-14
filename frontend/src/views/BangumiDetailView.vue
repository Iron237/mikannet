<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import Icon from '../components/Icon.vue'
import FileTags from '../components/FileTags.vue'
import SubscribeWizard from '../components/SubscribeWizard.vue'
import EditSubscriptionModal from '../components/EditSubscriptionModal.vue'

const route = useRoute()
const router = useRouter()
const b = ref(null)
const expanded = ref(new Set())
const showWizard = ref(false)
const confirmRemove = ref(false)
const removeFiles = ref(false)
const removing = ref(false)
const editSub = ref(null)        // 正在编辑的订阅(augmented for EditSubscriptionModal)
const delSub = ref(null)         // 待删订阅 { id, name }
const delSubFiles = ref(false)
const anidbMsg = ref('')
const anidbBusy = ref(false)

const KIND = { tv: ['tv', 'TV 连载'], movie: ['film', '剧场版'], ova: ['disc', 'OVA'] }
const EP_TYPE = { special: '特别篇', credits: 'OP/ED', trailer: 'PV/预告', other: '映像特典' }
const epStatus = {
  missing: ['未下载', ''], pending: ['等待中', 'blue'], downloading: ['下载中', 'accent'],
  completed: ['已完成', 'green'], archived: ['已入库', 'green'],
  download_error: ['错误', 'red'], submit_failed: ['失败', 'red'],
}

const realSubs = computed(() => (b.value?.subscriptions || []))

function epTitle(ep) {
  if (ep.type === 'regular') return `第 ${ep.number ?? '?'} 话`
  const label = EP_TYPE[ep.type] || ep.type
  return ep.number != null ? `${label} ${Math.round(ep.number)}` : label
}

function fmtTime(iso) {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

async function load() {
  b.value = await api.get(`/api/bangumi/${route.params.id}`)
}

function toggleExpand(id) {
  if (id == null) return
  expanded.value.has(id) ? expanded.value.delete(id) : expanded.value.add(id)
  expanded.value = new Set(expanded.value)
}

async function redownload(ep) {
  if (!ep.torrent_id) return
  await api.post(`/api/tasks/${ep.torrent_id}/resume`)
  await load()
}

async function doRemove() {
  removing.value = true
  await api.delete(`/api/bangumi/${b.value.id}?delete_files=${removeFiles.value}`)
  router.push('/')
}

const savingSeason = ref(false)
async function saveSeason() {
  savingSeason.value = true
  try { await api.patch(`/api/bangumi/${b.value.id}`, { season_number: b.value.season_number }) }
  finally { savingSeason.value = false }
}

// 形态手动覆盖(始终优先):元数据把电影/OVA 误判成 TV 时,一键改正,详情页布局随之切换
async function saveKind() {
  await api.patch(`/api/bangumi/${b.value.id}`, { kind: b.value.kind })
  await load()
}

async function toggleSub(s) {
  await api.patch(`/api/subscriptions/${s.id}`, { enabled: !s.enabled })
  await load()
}

function openEdit(s) {
  // 补齐 EditSubscriptionModal 需要的番剧上下文字段
  editSub.value = {
    ...s, bangumi_title: b.value.title, mikan_bangumi_id: b.value.mikan_bangumi_id,
    bangumi_eps_total: b.value.eps_total,
  }
}

async function confirmDelSub() {
  await api.delete(`/api/subscriptions/${delSub.value.id}?delete_files=${delSubFiles.value}`)
  delSub.value = null; delSubFiles.value = false
  await load()
}

async function syncAnidb() {
  anidbBusy.value = true; anidbMsg.value = ''
  try {
    const r = await api.post(`/api/bangumi/${b.value.id}/sync-anidb`, {})
    anidbMsg.value = r.ok
      ? (r.reason === 'cached' ? 'AniDB:24h 内已同步(缓存)'
         : `AniDB:已同步 ${r.episodes ?? 0} 集 · 形态 ${r.kind ?? ''}`)
      : (r.reason === 'no_match' ? 'AniDB:未匹配到该番剧(可手动绑定 aid)' : `AniDB:${r.reason}`)
    await load()
  } catch (e) { anidbMsg.value = e.message }
  finally { anidbBusy.value = false }
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
            <span class="tag accent kind"><Icon :name="KIND[b.kind]?.[0] || 'tv'" :size="13" /> {{ KIND[b.kind]?.[1] || b.kind }}</span>
            <span v-if="b.season" class="tag">{{ b.season }}</span>
            <span v-if="b.studio" class="tag">{{ b.studio }}</span>
            <span v-if="b.eps_total" class="tag">全 {{ b.eps_total }} 话</span>
            <span class="tag" :class="b.airing_status === 'airing' ? 'green' : ''">
              {{ b.airing_status === 'airing' ? '连载中' : '已完结' }}
            </span>
            <span v-if="b.score" class="tag accent"><Icon name="star" :size="12" /> {{ b.score }}</span>
          </div>
          <p class="summary muted" v-if="b.summary">{{ b.summary }}</p>
          <div class="row" style="margin-top: 12px; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span class="muted" style="font-size: 12.5px;">形态</span>
            <select class="input" style="width: 104px;" v-model="b.kind" @change="saveKind">
              <option value="tv">TV 连载</option>
              <option value="movie">剧场版</option>
              <option value="ova">OVA</option>
            </select>
            <span class="muted" style="font-size: 12.5px; margin-left: 6px;">Jellyfin 季号 Season</span>
            <input type="number" min="0" class="input" style="width: 64px;"
                   v-model.number="b.season_number" @change="saveSeason" />
            <span class="muted" style="font-size: 12px;">
              {{ savingSeason ? '保存中…' : '整理重命名用(SxxExx)' }}
            </span>
          </div>
          <div class="row" style="margin-top: 14px; flex-wrap: wrap;">
            <button class="btn primary" @click="showWizard = true"><Icon name="plus" /> 添加订阅</button>
            <button class="btn" :disabled="anidbBusy" @click="syncAnidb">
              <Icon name="refresh" /> {{ anidbBusy ? '同步中…' : '同步 AniDB 剧集' }}
            </button>
            <button class="btn danger" @click="confirmRemove = true"><Icon name="trash" /> 移除番剧</button>
          </div>
          <div v-if="anidbMsg" class="muted" style="margin-top: 8px; font-size: 12px;">{{ anidbMsg }}</div>
        </div>
      </div>
    </div>

    <div class="page">
      <!-- 订阅源详情 -->
      <div class="page-title">订阅源 <span class="muted" style="font-size: 13px; font-weight: 400;">{{ realSubs.length }} 个</span></div>
      <div v-if="!realSubs.length" class="muted" style="margin-bottom: 18px;">还没有订阅源 — 点「添加订阅」选字幕组</div>
      <div v-for="s in realSubs" :key="s.id" class="card sub-card">
        <div class="row" style="flex-wrap: wrap;">
          <Icon :name="s.is_local ? 'folder' : 'rss'" :size="16" class="muted" />
          <strong>{{ s.subgroup_name || s.mikan_subgroup_id }}</strong>
          <span v-if="s.is_local" class="tag">本地导入</span>
          <template v-else>
            <span class="health" :class="s.last_poll_ok ? 'ok' : 'bad'"
                  :title="s.last_poll_ok ? 'RSS 正常' : (s.last_poll_error || 'RSS 异常')"></span>
            <span class="tag" :class="s.enabled ? 'green' : ''">{{ s.enabled ? '启用中' : '已停用' }}</span>
          </template>
          <div class="spacer" />
          <template v-if="!s.is_local">
            <button class="btn sm" @click="toggleSub(s)">
              <Icon :name="s.enabled ? 'pause' : 'play'" :size="13" /> {{ s.enabled ? '停用' : '启用' }}
            </button>
            <button class="btn sm" @click="openEdit(s)"><Icon name="edit" :size="13" /> 编辑</button>
            <button class="btn sm danger" @click="delSub = { id: s.id, name: s.subgroup_name }">
              <Icon name="trash" :size="13" /> 删除
            </button>
          </template>
        </div>
        <div class="row sub-meta" style="flex-wrap: wrap;">
          <span class="tag">{{ s.backfill ? '补齐历史' : '只追新' }}</span>
          <span class="tag">{{ s.exclude_batch ? '排除合集' : '允许合集' }}</span>
          <span v-for="k in s.include_keywords" :key="'i' + k" class="tag green">+{{ k }}</span>
          <span v-for="k in s.exclude_keywords" :key="'e' + k" class="tag red">−{{ k }}</span>
          <span v-if="s.episode_offset" class="tag">偏移 {{ s.episode_offset }}</span>
          <div class="spacer" />
          <span v-if="!s.is_local && s.last_checked_at" class="muted" style="font-size: 11.5px;">
            <Icon name="clock" :size="12" /> {{ fmtTime(s.last_checked_at) }}
          </span>
        </div>
        <div v-if="!s.is_local && !s.last_poll_ok && s.last_poll_error" class="muted err">{{ s.last_poll_error }}</div>
      </div>

      <!-- 剧集 -->
      <div class="page-title" style="margin-top: 22px;">{{ b.kind === 'tv' ? '剧集' : '剧集 / 特典' }}</div>
      <div v-if="!b.episodes.length" class="muted">还没有剧集记录</div>
      <div v-for="ep in b.episodes" :key="(ep.type) + '-' + (ep.id ?? ep.number)" class="card ep-row"
           :class="{ missing: ep.status === 'missing' }">
        <div class="row" style="cursor: pointer;" @click="toggleExpand(ep.id)">
          <span v-if="ep.type !== 'regular'" class="tag accent" style="flex-shrink:0;">{{ EP_TYPE[ep.type] || ep.type }}</span>
          <strong class="ep-num">{{ epTitle(ep) }}</strong>
          <span v-if="ep.title" class="muted ep-title">{{ ep.title }}</span>
          <span class="tag" :class="epStatus[ep.status]?.[1]">{{ epStatus[ep.status]?.[0] ?? ep.status }}</span>
          <span v-if="ep.version > 1" class="tag accent">v{{ ep.version }}</span>
          <div class="spacer" />
          <span class="muted" style="font-size: 12px;" v-if="ep.files.length">{{ ep.files.length }} 个文件</span>
          <Icon v-if="ep.files.length" :name="expanded.has(ep.id) ? 'chevron-down' : 'chevron-right'" :size="15" class="muted" />
        </div>
        <div v-if="expanded.has(ep.id) && ep.files.length" class="files">
          <div v-for="f in ep.files" :key="f.id" class="file">
            <FileTags :file="f" :show-path="true" />
          </div>
        </div>
      </div>

      <!-- 影片 / 其他文件 -->
      <template v-if="b.unmapped_files && b.unmapped_files.length">
        <div class="page-title" style="margin-top: 22px;">
          {{ b.kind === 'tv' ? '其他文件' : '影片 / 版本' }}
          <span class="muted" style="font-size: 12px; font-weight: 400;">
            {{ b.kind === 'tv' ? '(已识别但未匹配到具体集 — 剧场版/合集/命名异常)' : '(影片本体与各版本)' }}
          </span>
        </div>
        <div v-for="f in b.unmapped_files" :key="f.id" class="card file unmapped">
          <FileTags :file="f" :show-path="true" />
        </div>
      </template>
    </div>

    <SubscribeWizard v-if="showWizard"
                     :preset="{ mikan_bangumi_id: b.mikan_bangumi_id, title: b.title }"
                     @close="showWizard = false; load()" />

    <EditSubscriptionModal v-if="editSub" :sub="editSub" @close="editSub = null; load()" />

    <!-- 删除订阅确认 -->
    <div v-if="delSub" class="modal-mask" @click.self="delSub = null">
      <div class="modal" style="width: 420px;">
        <h3 style="margin-bottom: 10px;">删除订阅源</h3>
        <p class="muted">将删除「{{ delSub.name }}」订阅及其下载任务记录(番剧与剧集保留)。</p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="delSubFiles" />
          同时删除已下载的文件(不可恢复)
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="delSub = null">取消</button>
          <button class="btn danger" @click="confirmDelSub">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 移除番剧确认 -->
    <div v-if="confirmRemove" class="modal-mask" @click.self="confirmRemove = false">
      <div class="modal" style="width: 440px;">
        <h3 style="margin-bottom: 10px;">移除番剧</h3>
        <p class="muted">将删除「{{ b.title }}」及其全部订阅、剧集记录和下载任务(下载器中一并移除)。</p>
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
.hero { background-size: cover; background-position: center top; position: relative; }
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
.kind { display: inline-flex; align-items: center; gap: 4px; }
.summary {
  margin-top: 12px; font-size: 13px; max-width: 720px;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
}
.sub-card { margin-bottom: 10px; padding: 13px 16px; }
.sub-meta { margin-top: 10px; gap: 6px; }
.health { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.health.ok { background: var(--green); box-shadow: 0 0 6px var(--green); }
.health.bad { background: var(--red); box-shadow: 0 0 6px var(--red); }
.err { color: var(--red); font-size: 12px; margin-top: 8px; word-break: break-all; }
.ep-row { margin-bottom: 8px; padding: 12px 16px; }
.ep-row.missing { opacity: .55; border-style: dashed; }
.ep-num { white-space: nowrap; }
.ep-title { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 280px; }

@media (max-width: 768px) {
  .hero-inner { flex-direction: column; align-items: center; text-align: center;
                padding-top: 24px; gap: 14px; }
  .hero-poster { width: 140px; }
  .hero-info h1 { font-size: 20px; }
  .hero-info .row { justify-content: center; }
  .summary { font-size: 12.5px; -webkit-line-clamp: 3; }
  .ep-title { display: none; }
}
.files { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 12px; }
.file.unmapped { padding: 12px 14px; margin-bottom: 8px; }
</style>
