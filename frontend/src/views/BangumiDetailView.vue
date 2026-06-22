<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { requestNative } from '../native'
import Icon from '../components/Icon.vue'
import FileTags from '../components/FileTags.vue'
import BdReleases from '../components/BdReleases.vue'
import BdImportWizard from '../components/BdImportWizard.vue'
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
// 智能下载 / BD
const autoBest = ref(false)
const bdOwned = ref(false)
const autoScan = ref(null)
let autoTimer = null
let mounted = true
// 文件管理
const opMsg = ref('')
const fileBusy = ref(0)          // 正在操作的文件 id
const fileEdit = ref(null)       // 正在归位编辑的文件 id
const editForm = ref({ type: 'regular', number: '' })
const delFile = ref(null)        // 待删文件
const delFileDisk = ref(false)

const KIND = { tv: ['tv', 'TV 连载'], movie: ['film', '剧场版'], ova: ['disc', 'OVA'] }
const EP_TYPE = { special: '特别篇', credits: 'OP/ED', trailer: 'PV/预告', other: '映像特典' }
// 正片导入向导
const importReleases = ref(null)   // 非空 = 打开向导(传入的发行数组)
function onImported() { importReleases.value = null; load() }
const EP_TYPE_OPTS = [['regular', '正片'], ['special', '特别篇'], ['credits', 'OP/ED'], ['trailer', 'PV/预告'], ['other', '其他']]
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
  autoBest.value = !!b.value.auto_best
  bdOwned.value = !!b.value.bd_owned
}

async function toggleBdOwned() {
  await api.patch(`/api/bangumi/${b.value.id}`, { bd_owned: bdOwned.value })
  await load()
}

// ---- 智能下载 ----
async function scanBest() {
  opMsg.value = ''
  try {
    await api.post(`/api/bangumi/${b.value.id}/auto-scan`, {})
    pollAuto()
  } catch (e) { opMsg.value = e.message }
}
async function pollAuto() {
  const s = await api.get('/api/bangumi/auto-scan/status')
  if (!mounted) return            // 卸载后别再起定时器/写已销毁组件
  autoScan.value = s
  if (autoScan.value.running) { autoTimer = setTimeout(pollAuto, 1500) }
  else { await load() }
}
async function toggleAutoBest() {
  await api.patch(`/api/bangumi/${b.value.id}`, { auto_best: autoBest.value })
}
const autoMine = computed(() =>
  (autoScan.value?.result || []).find(r => r.bangumi === b.value?.id))

// ---- 文件管理 ----
function startFileEdit(f, ep) {
  fileEdit.value = f.id
  editForm.value = { type: ep?.type || 'regular', number: ep?.number ?? '' }
}
async function saveFileEdit(f) {
  fileBusy.value = f.id; opMsg.value = ''
  try {
    await api.post(`/api/files/${f.id}/assign`, {
      type: editForm.value.type,
      episode_number: editForm.value.number === '' ? null : Number(editForm.value.number),
    })
    fileEdit.value = null
    await load()
  } catch (e) { opMsg.value = e.message } finally { fileBusy.value = 0 }
}
async function unassignFile(f) {
  fileBusy.value = f.id; opMsg.value = ''
  try { await api.post(`/api/files/${f.id}/unassign`, {}); await load() }
  catch (e) { opMsg.value = e.message } finally { fileBusy.value = 0 }
}
async function reprobeFile(f) {
  fileBusy.value = f.id; opMsg.value = ''
  try { await api.post(`/api/files/${f.id}/reprobe`, {}); await load() }
  catch (e) { opMsg.value = e.message } finally { fileBusy.value = 0 }
}

// 原生启动:未配置宿主机路径(url 空)或本机未装协议处理器 → 弹引导框(见 NativeLaunchModal)
function native(url) { requestNative(url) }
// 番剧外链
const bgmUrl = computed(() => b.value?.bgmtv_subject_id
  ? `https://bgm.tv/subject/${b.value.bgmtv_subject_id}` : null)
const moegirlUrl = computed(() => b.value?.title
  ? `https://zh.moegirl.org.cn/${encodeURIComponent(b.value.title)}` : null)
async function confirmDelFile() {
  const f = delFile.value
  opMsg.value = ''
  try {
    await api.delete(`/api/files/${f.id}?delete_disk=${delFileDisk.value}`)
    delFile.value = null; delFileDisk.value = false
    await load()
  } catch (e) { opMsg.value = e.message }
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
  try {
    await api.delete(`/api/bangumi/${b.value.id}?delete_files=${removeFiles.value}`)
    router.push('/')
  } catch (e) {
    opMsg.value = e.message
    removing.value = false
  }
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

onMounted(async () => {
  await load()
  const a = await api.get('/api/bangumi/auto-scan/status')
  if (a.running) { autoScan.value = a; pollAuto() }
})
onUnmounted(() => { mounted = false; clearTimeout(autoTimer) })
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
            <a v-if="bgmUrl" :href="bgmUrl" target="_blank" rel="noopener" class="tag ext-link">
              <Icon name="external" :size="12" /> Bangumi
            </a>
            <a v-if="b.mikan_url" :href="b.mikan_url" target="_blank" rel="noopener" class="tag ext-link"
               title="在蜜柑计划打开该番剧页面">
              <Icon name="external" :size="12" /> 蜜柑
            </a>
            <a :href="moegirlUrl" target="_blank" rel="noopener" class="tag ext-link"
               title="按中文名跳转萌娘百科,不存在则落到搜索/创建页">
              <Icon name="external" :size="12" /> 萌娘百科
            </a>
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
            <button v-if="b.mikan_bangumi_id" class="btn" :disabled="autoScan?.running" @click="scanBest"
                    title="扫所有字幕组,按偏好(BD>Web/分辨率/简中)补全缺集并升级现有源">
              <Icon name="zap" /> {{ autoScan?.running ? '扫描中…' : '扫描最佳源' }}
            </button>
            <label v-if="b.mikan_bangumi_id" class="row auto-toggle" title="开启后定期自动扫描补全/升级">
              <input type="checkbox" v-model="autoBest" @change="toggleAutoBest" /> 智能下载(常驻)
            </label>
            <label class="row auto-toggle" title="有原盘/已购 → 该番剧完全不自动下载">
              <input type="checkbox" v-model="bdOwned" @change="toggleBdOwned" /> 已购买(有原盘)
            </label>
            <button class="btn" :disabled="anidbBusy" @click="syncAnidb">
              <Icon name="refresh" /> {{ anidbBusy ? '同步中…' : '同步 AniDB 剧集' }}
            </button>
            <button class="btn danger" @click="confirmRemove = true"><Icon name="trash" /> 移除番剧</button>
          </div>
          <div v-if="anidbMsg" class="muted" style="margin-top: 8px; font-size: 12px;">{{ anidbMsg }}</div>
          <div v-if="autoScan && (autoScan.running || autoMine)" class="auto-status" style="margin-top: 8px;">
            <Icon name="zap" :size="13" style="color: var(--accent);" />
            <span v-if="autoScan.running">智能扫描中…</span>
            <span v-else-if="autoMine">智能扫描完成 —
              {{ autoMine.submitted ? `提交 ${autoMine.submitted} 个种子(集 ${(autoMine.needed||[]).join(', ')})` : (autoMine.note || '无需下载') }}
            </span>
          </div>
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
      <p v-if="opMsg" class="op-msg">{{ opMsg }}</p>
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
          <template v-if="ep.files.length">
            <button class="btn xs" title="用默认播放器播放" @click.stop="native(ep.files[0].play_url)">
              <Icon name="play" :size="12" /> 播放
            </button>
            <button class="btn xs" title="在资源管理器中打开" @click.stop="native(ep.files[0].reveal_url)">
              <Icon name="folder-open" :size="12" /> 打开目录
            </button>
          </template>
          <span class="muted" style="font-size: 12px;" v-if="ep.files.length">{{ ep.files.length }} 个文件</span>
          <Icon v-if="ep.files.length" :name="expanded.has(ep.id) ? 'chevron-down' : 'chevron-right'" :size="15" class="muted" />
        </div>
        <div v-if="expanded.has(ep.id) && ep.files.length" class="files">
          <div v-for="f in ep.files" :key="f.id" class="file">
            <FileTags :file="f" :show-path="true" />
            <div class="file-ops">
              <button class="btn xs" title="用默认播放器播放" @click="native(f.play_url)"><Icon name="play" :size="12" /> 播放</button>
              <button class="btn xs" title="在资源管理器中打开" @click="native(f.reveal_url)"><Icon name="folder-open" :size="12" /> 打开目录</button>
              <button class="btn xs" :disabled="fileBusy === f.id" @click="reprobeFile(f)"><Icon name="refresh" :size="12" /> 重探测</button>
              <button class="btn xs" @click="startFileEdit(f, ep)"><Icon name="folder-in" :size="12" /> 归位/改类型</button>
              <button class="btn xs" :disabled="fileBusy === f.id" @click="unassignFile(f)">移出</button>
              <button class="btn xs danger" @click="delFile = f"><Icon name="trash" :size="12" /> 删除</button>
            </div>
            <div v-if="fileEdit === f.id" class="file-edit row">
              <select class="input" v-model="editForm.type" style="width: 96px;">
                <option v-for="o in EP_TYPE_OPTS" :key="o[0]" :value="o[0]">{{ o[1] }}</option>
              </select>
              <input class="input" type="number" v-model="editForm.number" placeholder="集号(正片必填)" style="width: 140px;" />
              <button class="btn xs primary" :disabled="fileBusy === f.id" @click="saveFileEdit(f)">保存</button>
              <button class="btn xs" @click="fileEdit = null">取消</button>
            </div>
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
        <div v-for="f in b.unmapped_files" :key="'u' + f.id" class="card file unmapped">
          <FileTags :file="f" :show-path="true" />
          <div class="file-ops">
            <button class="btn xs" title="用默认播放器播放" @click="native(f.play_url)"><Icon name="play" :size="12" /> 播放</button>
            <button class="btn xs" title="在资源管理器中打开" @click="native(f.reveal_url)"><Icon name="folder-open" :size="12" /> 打开目录</button>
            <button class="btn xs" :disabled="fileBusy === f.id" @click="reprobeFile(f)"><Icon name="refresh" :size="12" /> 重探测</button>
            <button class="btn xs" @click="startFileEdit(f)"><Icon name="folder-in" :size="12" /> 归位到集</button>
            <button class="btn xs danger" @click="delFile = f"><Icon name="trash" :size="12" /> 删除</button>
          </div>
          <div v-if="fileEdit === f.id" class="file-edit row">
            <select class="input" v-model="editForm.type" style="width: 96px;">
              <option v-for="o in EP_TYPE_OPTS" :key="o[0]" :value="o[0]">{{ o[1] }}</option>
            </select>
            <input class="input" type="number" v-model="editForm.number" placeholder="集号(正片必填)" style="width: 140px;" />
            <button class="btn xs primary" :disabled="fileBusy === f.id" @click="saveFileEdit(f)">保存</button>
            <button class="btn xs" @click="fileEdit = null">取消</button>
          </div>
        </div>
      </template>

      <!-- BD 发行(导入按钮在每套发行行内,见 BdReleases)-->
      <template v-if="b.bd_releases && b.bd_releases.length">
        <div class="page-title" style="margin-top: 22px;">BD 发行
          <span class="muted" style="font-size: 12px; font-weight: 400;">
            (正片可导入替换 web;特典 / 扫描 / CD 点「打开目录」用本机应用浏览)
          </span>
        </div>
        <BdReleases :releases="b.bd_releases" @import="ir => importReleases = [ir]" />
      </template>
    </div>

    <SubscribeWizard v-if="showWizard"
                     :preset="{ mikan_bangumi_id: b.mikan_bangumi_id, title: b.title }"
                     @close="showWizard = false; load()" />

    <EditSubscriptionModal v-if="editSub" :sub="editSub" @close="editSub = null; load()" />

    <BdImportWizard v-if="importReleases" :releases="importReleases"
                    @close="importReleases = null" @done="onImported" />

    <!-- 删除文件确认 -->
    <div v-if="delFile" class="modal-mask" @click.self="delFile = null">
      <div class="modal" style="width: 460px;">
        <h3 style="margin-bottom: 10px;">删除文件</h3>
        <p class="muted" style="font-size: 12.5px; word-break: break-all;">{{ delFile.name }}</p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="delFileDisk" />
          同时删除 NAS 上的磁盘文件(做种中的种子谨慎,可能触发重新校验)
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="delFile = null">取消</button>
          <button class="btn danger" @click="confirmDelFile">确认删除</button>
        </div>
      </div>
    </div>

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
.ext-link { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; cursor: pointer; }
.ext-link:hover { border-color: var(--accent); color: var(--accent); }
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
.auto-toggle { gap: 6px; cursor: pointer; font-size: 12.5px; align-items: center;
  border: 1px solid var(--border); border-radius: 8px; padding: 0 10px; }
.auto-toggle input { accent-color: var(--accent); }
.auto-status { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text-dim); }
.op-msg { color: var(--red); font-size: 12.5px; margin-bottom: 10px; }
.file-ops { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.file-edit { margin-top: 8px; gap: 8px; flex-wrap: wrap; align-items: center; }
.btn.xs { font-size: 11.5px; padding: 3px 8px; }
.file-ops input[type=checkbox] { accent-color: var(--accent); }
</style>
