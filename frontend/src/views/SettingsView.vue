<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, fmtSize } from '../api'
import Icon from '../components/Icon.vue'
import FileBrowserModal from '../components/FileBrowserModal.vue'
import { launch as launchNative } from '../native'

const health = ref(null)
const cfg = ref({})            // key -> { value, group, type, secret }
const channels = ref([])
const saving = ref('')
const cfgSaved = ref('')
const testResult = ref({})
const loadError = ref('')

const LABELS = {
  poll_interval_min: 'RSS 轮询间隔(分钟)',
  tmdb_api_key: 'TMDB API Key',
  downloader: '下载器后端',
  qb_host: 'qB 地址', qb_port: 'qB 端口', qb_username: 'qB 用户名', qb_password: 'qB 密码',
  bitcomet_host: 'BitComet 地址', bitcomet_port: 'BitComet 端口',
  bitcomet_username: 'BitComet 用户名', bitcomet_password: 'BitComet 密码',
  bitcomet_download_root: 'BitComet 保存根目录',
  download_root: '应用下载根目录(qB 保存路径)',
  proxy_url: '代理地址',
  mikan_base_url: 'Mikan 域名', nyaa_base_url: 'nyaa 域名', dmhy_base_url: 'dmhy 域名',
  organize_enabled: '整理到 Jellyfin 结构(qB 原地重命名)',
  nfo_enabled: '写 tvshow.nfo + 封面/背景图',
  dead_torrent_enabled: '自动清理坏种(无做种且卡住 → 删除换源)',
  dead_torrent_hours: '坏种判定:卡住超过几小时',
  stall_pause_enabled: '无进度自动暂停(长期不增长 → 暂停,不删,可恢复)',
  stall_pause_hours: '无进度判定:进度停滞超过几小时',
  llm_enabled: '启用 LLM 兜底解析(仅低置信度调用)',
  llm_base_url: 'LLM baseURL(OpenAI 兼容)', llm_api_key: 'LLM API Key', llm_model: 'LLM 模型',
  anidb_enabled: '启用 AniDB 剧集元数据(需注册 client 名)',
  anidb_client_name: 'AniDB client 名(在 anidb.net 注册)',
  anidb_client_ver: 'AniDB client 版本号',
  anidb_search_base: 'anidb-search 地址(番剧→aid,可自托管)',
  anidb_lang: '剧集名首选语言(zh-Hans / en / x-jat)',
  auto_dl_resolution: '目标分辨率(严格匹配,如 1080p)',
  auto_dl_sub_lang: '字幕语言要求(简中 = 必须含简体)',
  auto_dl_prefer_bd: '片源优先 BD > Web(并把已有 Web 升级为 BD)',
  auto_dl_interval_min: '定期自动补全扫描间隔(分钟,0=关闭)',
  media_host_root: '番剧库文件夹路径(你电脑上看到的,如 Z:\\番剧\\mikannet)',
  bd_owned_host_root: '已购原盘文件夹路径(如 Z:\\BD\\已购BD翻录)',
  data_host_root: 'data 目录路径(用于「打开 log 目录」,如 C:\\mikannet\\data\\mikannet)',
  powerdvd_path: 'PowerDVD.exe 路径(留空 → 自动探测常见安装位)',
}
const sections = [
  { id: 'common', name: '常用与存储', icon: 'database', desc: '状态、下载目录和下载器' },
  { id: 'automation', name: '订阅与自动化', icon: 'rss', desc: '轮询、补齐、整理和坏种' },
  { id: 'playback', name: '播放与文件', icon: 'play', desc: '本机播放器、资源管理器和网页备用' },
  { id: 'services', name: '网络与元数据', icon: 'link', desc: '代理、搜索源、元数据和通知' },
  { id: 'maintenance', name: '维护', icon: 'settings', desc: '更新、备份和迁移' },
]
const activeSection = ref('common')
const GROUP_ORDER = ['常规', '下载器', '自动补全与升级', '整理', '坏种清理',
  '播放', '代理', '搜索源', 'bgm.tv 联动', 'AniDB', 'LLM']
const SECTION_GROUPS = {
  common: ['常规', '下载器'],
  automation: ['自动补全与升级', '整理', '坏种清理'],
  playback: ['播放'],
  services: ['代理', '搜索源', 'bgm.tv 联动', 'AniDB', 'LLM'],
  maintenance: [],
}

const channelMeta = {
  telegram: { name: 'Telegram Bot', fields: [['bot_token', 'Bot Token'], ['chat_id', 'Chat ID']] },
  serverchan: { name: 'Server酱', fields: [['send_key', 'SendKey (SCT…)']] },
  pushplus: { name: 'PushPlus', fields: [['token', 'Token']] },
}
const eventLabels = { on_new: '检测到更新', on_start: '开始下载', on_complete: '下载完成', on_fail: '下载失败' }
const downloaderNames = { qb: 'qBittorrent', bitcomet: 'BitComet' }

const groups = computed(() => {
  const m = {}
  for (const [key, o] of Object.entries(cfg.value)) {
    (m[o.group] ??= []).push({ key, ...o })
  }
  const allowed = SECTION_GROUPS[activeSection.value] || []
  return GROUP_ORDER.filter(g => m[g] && allowed.includes(g))
    .map(g => ({ group: g, items: m[g] }))
})

async function load() {
  loadError.value = ''
  try {
    const [h, c, n] = await Promise.all([
      api.get('/api/system/health'), api.get('/api/config'), api.get('/api/notifications'),
    ])
    health.value = h
    cfg.value = c
    channels.value = n
  } catch (e) { loadError.value = e.message }
}

async function saveConfig() {
  cfgSaved.value = '保存中…'
  try {
    // 注意:表单 v-model 绑的是 groups 里的副本({key,...o}),不是 cfg.value 原对象。
    // 必须从 groups 读用户改过的值,否则会提交旧值、保存后回退。
    const payload = {}
    for (const g of groups.value) for (const it of g.items) payload[it.key] = it.value
    const r = await api.put('/api/config', payload)
    cfgSaved.value = `已保存并生效(${r.applied.length} 项)`
    await load()
    return true
  } catch (e) {
    cfgSaved.value = '保存失败:' + e.message
    return false
  }
}

async function saveCh(ch) {
  saving.value = ch.channel
  try {
    await api.put(`/api/notifications/${ch.channel}`, ch)
    testResult.value = { ...testResult.value, [ch.channel]: '已保存' }
    return true
  } catch (e) {
    testResult.value = { ...testResult.value, [ch.channel]: '保存失败:' + e.message }
    return false
  } finally { saving.value = '' }
}
async function testCh(ch) {
  testResult.value = { ...testResult.value, [ch.channel]: '发送中…' }
  try {
    if (!await saveCh(ch)) return
    await api.post(`/api/notifications/${ch.channel}/test`)
    testResult.value = { ...testResult.value, [ch.channel]: '发送成功' }
  } catch (e) { testResult.value = { ...testResult.value, [ch.channel]: '发送失败:' + e.message } }
}
async function pollNow() { await api.post('/api/system/poll') }

// ---- 自更新(检查 / 一键更新)----
const ver = ref({ version: '', base_rev: '' })
const updCheck = ref(null)         // /update/check 结果
const updChecking = ref(false)
const updApplying = ref(false)
const updMsg = ref('')
const updStatus = ref(null)        // /update/status 进度
let updTimer = null
const PHASE_LABEL = {
  downloading: '下载代码包', verifying: '校验完整性', applying: '应用新版本',
  recreating: '拉取新镜像 / 重建容器', restarting: '重启中',
}
async function loadVersion() { try { ver.value = await api.get('/api/system/version') } catch { /* ignore */ } }
async function checkUpdate() {
  updChecking.value = true; updMsg.value = ''; updCheck.value = null
  try { updCheck.value = await api.get('/api/system/update/check') }
  catch (e) { updMsg.value = '检查失败:' + e.message }
  finally { updChecking.value = false }
}
const prerelease = computed({
  get: () => !!cfg.value.update_channel_prerelease?.value,
  set: (v) => {
    if (cfg.value.update_channel_prerelease) cfg.value.update_channel_prerelease.value = v
    api.put('/api/config', { update_channel_prerelease: v }).catch(() => {})
    checkUpdate()
  },
})
async function applyUpdate() {
  if (!updCheck.value || updCheck.value.type === 'none') return
  const full = updCheck.value.type === 'full'
  const ok = window.confirm(full
    ? `完整更新(换镜像)到 ${updCheck.value.latest}:拉新镜像并重建容器,期间短暂不可用。确定?`
    : `更新到 ${updCheck.value.latest}:自动下载、校验并重启,期间短暂不可用。确定?`)
  if (!ok) return
  updApplying.value = true; updMsg.value = '正在启动更新…'; updStatus.value = null
  try {
    const r = await api.post('/api/system/update/apply')
    updMsg.value = r.type === 'full' ? '完整更新中(换镜像)…' : '更新中…'
    updTimer = setTimeout(pollUpdateStatus, 1000)
  } catch (e) { updApplying.value = false; updMsg.value = '更新失败:' + e.message }
}
async function pollUpdateStatus() {
  try {
    updStatus.value = await api.get('/api/system/update/status')
    if (updStatus.value.phase === 'error') {
      updApplying.value = false; updMsg.value = '更新失败:' + updStatus.value.error; return
    }
    if (updStatus.value.phase === 'restarting') { waitForRestart(); return }
  } catch {
    // 后端可能已开始重启 → 转为等待新版本起来
    waitForRestart(); return
  }
  updTimer = setTimeout(pollUpdateStatus, 1200)
}
function waitForRestart() {
  updMsg.value = '应用重启中,等待新版本起来…'
  const target = updCheck.value?.latest
  const tick = async () => {
    try {
      const v = await api.get('/api/system/version')
      if (!target || v.version === target) {
        ver.value = v; updApplying.value = false
        updMsg.value = `更新完成:当前 ${v.version}`
        // current 也要同步成新版本(否则「已是最新(vX)」仍显示更新前的版本);
        // 系统状态卡的版本来自 /health,更新后一并刷新,别停在旧值。
        updCheck.value = { type: 'none', current: v.version, latest: v.version }
        try { health.value = await api.get('/api/system/health') } catch { /* 保留旧值 */ }
        return
      }
    } catch { /* 仍在重启,继续等 */ }
    updTimer = setTimeout(tick, 2000)
  }
  tick()
}
onUnmounted(() => clearTimeout(updTimer))

// 先保存配置(让 .bat 嵌入当前路径前缀 + 令牌),再下载自安装协议处理器
async function downloadHandler() {
  if (!await saveConfig()) return
  // 带当前访问地址 → 安装器写浏览器免询问策略(根治每次播放弹窗)
  window.location.href = `/api/launch/handler.bat?origin=${encodeURIComponent(window.location.origin)}`
}

// ---- 存储(NAS / SMB,可在此重配;复用首次向导的端点)----
const stor = ref({
  mode: 'smb',
  local_host_path: '',
  smb_host_path: '',
  smb_username: '',
  smb_password: '',
  smb_vers: '3.0',
  nfs_host_path: '',
  nfs_options: 'vers=4,soft,timeo=30,retrans=2',
})
const storState = ref(null)
const storMsg = ref('')
const storBusy = ref(false)
const fileBrowserOpen = ref(false)
let pickerTimer = null

function applyNasPreset(vendor) {
  const presets = {
    synology: { mode: 'smb', path: '//NAS/共享文件夹/Anime', msg: '群晖 DSM：控制面板 → 文件服务 → SMB；也可切 NFS 填 NAS:/volume1/Anime。' },
    qnap: { mode: 'smb', path: '//QNAP/Multimedia/Anime', msg: 'QNAP：控制台 → 网络和文件服务 → Win/Mac/NFS；NFS 常用 QNAP:/share/Anime。' },
    truenas: { mode: 'nfs', path: 'truenas:/mnt/tank/Anime', msg: 'TrueNAS：推荐 NFSv4；也可创建 SMB Share 后使用 //truenas/Anime。' },
    unraid: { mode: 'smb', path: '//tower/media/Anime', msg: 'Unraid：共享名通常位于 //tower/<share>；NFS 可用 tower:/mnt/user/media。' },
    windows: { mode: 'smb', path: '//电脑名/共享名/Anime', msg: 'Windows 共享：文件夹属性 → 共享；请使用电脑名或局域网 IP。' },
  }
  const p = presets[vendor]
  stor.value.mode = p.mode
  if (p.mode === 'smb') stor.value.smb_host_path = p.path
  else stor.value.nfs_host_path = p.path
  storMsg.value = p.msg
}

async function pickWindowsFolder(target = 'local_host_path') {
  clearTimeout(pickerTimer)
  const requestId = (crypto.randomUUID?.() || `${Date.now()}${Math.random()}`).replace(/-/g, '')
  storMsg.value = '正在打开 Windows 文件夹选择器…'
  try {
    const r = await api.get(`/api/launch/picker?request_id=${encodeURIComponent(requestId)}`)
    launchNative(r.url)
  } catch (e) {
    storMsg.value = `无法启动选择器：${e.message}。可直接粘贴 Windows 路径。`
    return
  }
  let attempts = 0
  const poll = async () => {
    attempts++
    try {
      const r = await api.get(`/api/launch/selection/${encodeURIComponent(requestId)}`)
      if (r.ready && r.path) {
        if (target === 'local_host_path') stor.value.local_host_path = r.path
        else if (cfg.value[target]) cfg.value[target].value = r.path
        storMsg.value = `已选择：${r.path}`
        return
      }
    } catch { /* 继续等待 */ }
    if (attempts < 80) pickerTimer = setTimeout(poll, 500)
    else storMsg.value = '未收到选择结果；Windows 集成未安装时可直接粘贴路径。'
  }
  pickerTimer = setTimeout(poll, 500)
}

function isWindowsPathKey(key) {
  return ['media_host_root', 'bd_owned_host_root', 'data_host_root'].includes(key)
}

async function loadStorage() {
  try {
    const s = await api.get('/api/setup/storage')
    storState.value = s
    stor.value.mode = s.mode || 'smb'
    stor.value.local_host_path = s.local_host_path || ''
    stor.value.smb_host_path = s.smb_host_path || ''
    stor.value.smb_username = s.smb_username || ''
    stor.value.smb_vers = s.smb_vers || '3.0'
    stor.value.nfs_host_path = s.nfs_host_path || ''
    stor.value.nfs_options = s.nfs_options || 'vers=4,soft,timeo=30,retrans=2'
  } catch { /* ignore */ }
}
async function testStorage() {
  storBusy.value = true; storMsg.value = '测试中…'
  try {
    const r = await api.post('/api/setup/storage/test', stor.value)
    storMsg.value = r.ok
      ? `可连接${r.writable === false ? '(只读!写入失败)' : '·可写'};样例:${(r.sample || []).slice(0, 3).join(' / ') || '(空)'}`
      : '失败:' + r.error
  } catch (e) { storMsg.value = '失败:' + e.message } finally { storBusy.value = false }
}
async function saveStorage() {
  storBusy.value = true; storMsg.value = '保存并连接中…'
  try {
    await api.post('/api/setup/storage', stor.value)
    if (stor.value.mode === 'local' && stor.value.local_host_path) {
      await api.put('/api/config', { media_host_root: stor.value.local_host_path })
    }
    storMsg.value = '已保存并连接'
    await loadStorage()
  } catch (e) { storMsg.value = '失败:' + e.message } finally { storBusy.value = false }
}
// 网络存储断线留下僵尸挂载导致「未连接」时,按现有配置一键重挂(不改配置)
async function remountStorage() {
  storBusy.value = true; storMsg.value = '重新挂载中…'
  try {
    await api.post('/api/setup/storage/remount', {})
    storMsg.value = '已重新挂载'
    await loadStorage()
  } catch (e) { storMsg.value = '失败:' + e.message } finally { storBusy.value = false }
}

// ---- 数据备份 / 迁移 ----
const backupSettings = ref(false)
const importFile = ref(null)
const backupMsg = ref('')
function exportData() {
  window.location.href = '/api/backup/export' + (backupSettings.value ? '?include_settings=1' : '')
}
function onImportFile(e) { importFile.value = e.target.files[0] || null; backupMsg.value = '' }
async function importData() {
  if (!importFile.value) return
  let data
  try { data = JSON.parse(await importFile.value.text()) }
  catch { backupMsg.value = '文件解析失败(不是有效 JSON 备份)'; return }
  // 是否含设置由备份文件本身决定(导出时勾了才有),导入自动还原,无需再勾选
  const hasSettings = !!data.settings || !!(data.tables && data.tables.notification_config)
  const extra = hasSettings ? ',并还原设置/通知(API key/cookie/偏好,不含本机连接与路径)' : ''
  if (!window.confirm(`导入会用备份覆盖当前的番剧库 / 订阅 / 剧集 / 下载记录 / 文件路径${extra}。确定继续?`)) return
  backupMsg.value = '导入中…'
  try {
    const r = await api.post('/api/backup/import', data)
    backupMsg.value = `导入完成:共写入 ${r.total} 条${r.settings_applied ? `(含设置 ${r.settings_applied} 项)` : ''}。封面没显示就点下方「重新拉取封面/元数据」。`
  } catch (e) { backupMsg.value = '导入失败:' + e.message }
}

// 迁移后封面/banner 没带过来 → 重新从 bgm.tv/Mikan/TMDB 拉(只补缺失的图)
const refreshMeta = ref(null)
let refreshTimer = null
async function refetchCovers() {
  backupMsg.value = ''
  try { await api.post('/api/bangumi/refresh-metadata-all', {}); pollRefresh() }
  catch (e) { backupMsg.value = e.message }
}
async function pollRefresh() {
  refreshMeta.value = await api.get('/api/bangumi/refresh-metadata-all/status')
  if (refreshMeta.value.running) refreshTimer = setTimeout(pollRefresh, 1500)
}
onUnmounted(() => {
  clearTimeout(refreshTimer)
  clearTimeout(pickerTimer)
})

onMounted(() => { load(); loadStorage(); loadVersion() })
</script>

<template>
  <div class="page">
    <div class="page-title">设置</div>
    <p v-if="loadError" class="load-error">
      配置加载失败:{{ loadError }}
      <button class="btn sm" @click="load"><Icon name="refresh" :size="13" /> 重试</button>
    </p>

    <nav class="settings-nav" aria-label="设置分类">
      <button v-for="section in sections" :key="section.id" class="settings-tab"
              :class="{ on: activeSection === section.id }"
              @click="activeSection = section.id">
        <Icon :name="section.icon" :size="16" />
        <span><strong>{{ section.name }}</strong><small>{{ section.desc }}</small></span>
      </button>
    </nav>

    <div v-if="activeSection === 'common'" class="card" style="margin-bottom: 16px;">
      <div class="row health-row">
        <h3 style="margin: 0;">系统状态</h3>
        <span class="tag" :class="health?.status === 'ok' ? 'green' : 'red'" v-if="health">
          {{ downloaderNames[health.downloader] || health.downloader }} {{ health.status === 'ok' ? '已连接' : '不可达' }}
        </span>
        <span class="muted" v-if="health?.info"> {{ health.info.version }} </span>
        <div class="spacer" />
        <button class="btn sm" @click="pollNow">立即检查订阅更新</button>
      </div>
    </div>

    <!-- 自更新:检查更新 + 一键更新 -->
    <div v-if="activeSection === 'maintenance'" class="card" style="margin-bottom: 16px;">
      <div class="row update-head" style="margin-bottom: 10px;">
        <h3 style="margin: 0; font-size: 15px;">更新</h3>
        <span class="tag">当前 v{{ ver.version || '—' }}</span>
        <label class="row" style="gap: 5px; cursor: pointer; font-size: 12.5px;">
          <input type="checkbox" v-model="prerelease" /> 包含预发布
        </label>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;">{{ updMsg }}</span>
        <button class="btn sm" :disabled="updChecking || updApplying" @click="checkUpdate">
          {{ updChecking ? '检查中…' : '检查更新' }}
        </button>
      </div>
      <template v-if="updCheck">
        <div v-if="updCheck.type === 'none'" class="muted" style="font-size: 12.5px;">
          已是最新(v{{ updCheck.current }})。
        </div>
        <div v-else>
          <div class="row" style="gap: 8px; flex-wrap: wrap; margin-bottom: 8px;">
            <span class="tag green">新版 v{{ updCheck.latest }}</span>
            <span class="tag" :class="updCheck.type === 'full' ? 'red' : ''">
              {{ updCheck.type === 'full' ? '完整更新(换镜像)' : '纯代码更新' }}
            </span>
            <span v-if="updCheck.prerelease" class="tag">预发布</span>
            <span v-if="updCheck.size" class="muted" style="font-size: 12px;">{{ fmtSize(updCheck.size) }}</span>
            <div class="spacer" />
            <button class="btn primary sm" :disabled="updApplying" @click="applyUpdate">
              {{ updApplying ? '更新中…' : '立即更新' }}
            </button>
          </div>
          <pre v-if="updCheck.changelog" class="changelog">{{ updCheck.changelog }}</pre>
        </div>
      </template>
      <div v-if="updApplying && updStatus" class="muted" style="font-size: 12px; margin-top: 8px;">
        {{ PHASE_LABEL[updStatus.phase] || updStatus.phase }}<template
          v-if="updStatus.phase === 'downloading' && updStatus.progress"> · {{ updStatus.progress }}%</template>
      </div>
    </div>

    <!-- 存储：本地绑定 / SMB / NFS -->
    <div v-if="activeSection === 'common'" class="card storage-card" style="margin-bottom: 16px;">
      <div class="row" style="margin-bottom: 10px;">
        <h3 style="margin: 0; font-size: 15px;">存储</h3>
        <span v-if="storState" class="tag" :class="storState.mounted ? 'green' : 'red'">
          {{ storState.mounted ? '已连接' : '未连接' }}
        </span>
        <span v-if="storState?.error" class="muted" style="font-size: 12px; color: var(--red);">{{ storState.error }}</span>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;">{{ storMsg }}</span>
      </div>
      <div class="storage-modes">
        <label :class="{ on: stor.mode === 'local' }"><input type="radio" value="local" v-model="stor.mode" />
          <Icon name="folder" :size="15" /> Windows / 本地目录</label>
        <label :class="{ on: stor.mode === 'smb' }"><input type="radio" value="smb" v-model="stor.mode" />
          <Icon name="database" :size="15" /> SMB / CIFS</label>
        <label :class="{ on: stor.mode === 'nfs' }"><input type="radio" value="nfs" v-model="stor.mode" />
          <Icon name="database" :size="15" /> NFS v3/v4</label>
      </div>
      <div class="nas-presets">
        <span class="muted">快速示例</span>
        <button class="preset" @click="applyNasPreset('synology')">群晖 Synology</button>
        <button class="preset" @click="applyNasPreset('qnap')">QNAP</button>
        <button class="preset" @click="applyNasPreset('truenas')">TrueNAS</button>
        <button class="preset" @click="applyNasPreset('unraid')">Unraid</button>
        <button class="preset" @click="applyNasPreset('windows')">Windows 共享</button>
      </div>
      <div v-if="stor.mode === 'local'" class="cfg-grid">
        <label class="cfg-field full">
          <span>Windows / 宿主机媒体目录</span>
          <div class="path-input">
            <input class="input" v-model="stor.local_host_path" placeholder="D:\Anime\Mikannet 或 Z:\番剧\mikannet" />
            <button class="btn sm" type="button" @click="pickWindowsFolder('local_host_path')">
              <Icon name="folder-open" :size="13" /> 浏览
            </button>
          </div>
          <small>Docker 中固定映射为 <code>/downloads</code>；更换宿主目录后需按部署配置重建容器。</small>
        </label>
      </div>
      <div v-else-if="stor.mode === 'smb'" class="cfg-grid">
        <label class="cfg-field"><span>共享地址(//主机/共享)</span><input class="input" v-model="stor.smb_host_path" placeholder="//192.168.1.100/anime/mikannet" /></label>
        <label class="cfg-field"><span>SMB 版本</span><input class="input" v-model="stor.smb_vers" placeholder="3.0" /></label>
        <label class="cfg-field"><span>用户名</span><input class="input" v-model="stor.smb_username" /></label>
        <label class="cfg-field"><span>密码(留空=不改)</span><input class="input" type="password" v-model="stor.smb_password" placeholder="留空保留原密码" /></label>
      </div>
      <div v-else class="cfg-grid">
        <label class="cfg-field"><span>NFS 导出地址</span><input class="input" v-model="stor.nfs_host_path" placeholder="nas:/volume1/anime/mikannet" /></label>
        <label class="cfg-field"><span>NFS 挂载选项</span><input class="input" v-model="stor.nfs_options" placeholder="vers=4,soft,timeo=30,retrans=2" /></label>
      </div>
      <div class="row" style="gap: 10px; margin-top: 10px;">
        <button class="btn sm" :disabled="storBusy" @click="testStorage">测试连接</button>
        <button class="btn primary sm" :disabled="storBusy" @click="saveStorage">保存并连接</button>
        <button v-if="stor.mode === 'smb' || stor.mode === 'nfs'" class="btn sm" :disabled="storBusy" @click="remountStorage"
                 title="断线后留下僵尸挂载导致「未连接」时,按现有配置重挂(不改配置)">
          <Icon name="refresh" :size="13" /> 重新挂载
        </button>
        <button class="btn sm" @click="fileBrowserOpen = true">
          <Icon name="folder-open" :size="13" /> 浏览当前媒体库
        </button>
      </div>
      <p class="storage-note">
        应用只访问容器内 <code>{{ storState?.download_root_local || '/downloads' }}</code>。
        Windows 路径用于部署绑定和本机定位；网页不能绕过 Docker 直接读取任意磁盘。
      </p>
    </div>

    <!-- 通用配置(DB 覆盖 env,改完即时生效) -->
    <div v-if="groups.length" class="row" style="margin: 8px 0 12px;">
      <h3 style="margin: 0; font-size: 15px;">配置</h3>
      <span class="muted" style="font-size: 12px;">改完点保存即时生效,无需重启</span>
      <div class="spacer" />
      <span class="muted" style="font-size: 12.5px;">{{ cfgSaved }}</span>
      <button class="btn primary sm" @click="saveConfig">保存配置</button>
    </div>
    <div v-for="g in groups" :key="g.group" class="card" style="margin-bottom: 12px;">
      <h4 style="margin: 0 0 12px; color: var(--accent);">{{ g.group }}</h4>
      <div class="cfg-grid">
        <label v-for="it in g.items" :key="it.key" class="cfg-field"
               :class="{ toggle: it.type === 'bool' }">
          <span>{{ LABELS[it.key] || it.key }}</span>
          <input v-if="it.type === 'bool'" type="checkbox" v-model="it.value" />
          <select v-else-if="it.key === 'downloader'" class="input" v-model="it.value">
            <option value="qb">qBittorrent</option>
            <option value="bitcomet">BitComet</option>
          </select>
          <input v-else-if="it.type === 'int'" type="number" class="input" v-model.number="it.value" />
          <div v-else-if="isWindowsPathKey(it.key)" class="path-input">
            <input class="input" v-model="it.value"
                   :type="it.secret ? 'password' : 'text'"
                   :placeholder="it.secret ? '已设置(留空不改)' : ''" />
            <button class="btn sm" type="button" @click="pickWindowsFolder(it.key)">
              <Icon name="folder-open" :size="13" /> 浏览
            </button>
          </div>
          <input v-else class="input" v-model="it.value"
                 :type="it.secret ? 'password' : 'text'"
                 :placeholder="it.secret ? '已设置(留空不改)' : ''" />
        </label>
      </div>
    </div>

    <!-- 本机播放:协议处理器 -->
    <div v-if="activeSection === 'playback'" class="card" style="margin-bottom: 12px;">
      <h4 style="margin: 0 0 8px; color: var(--accent);">本机播放与文件定位（默认）</h4>
      <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
        详情页“本机播放”会交给 Windows 默认播放器；把视频文件默认应用设为 PotPlayer 后即可直接使用。
        “打开位置”会调用文件资源管理器并选中对应文件。请先在上方填写这台电脑实际看到的媒体路径。
      </p>
      <div class="row" style="margin-top: 10px;">
        <button class="btn primary sm" @click="downloadHandler">
          保存配置并下载 Windows 集成修复包
        </button>
        <span class="muted" style="font-size: 12px;">{{ cfgSaved }}</span>
      </div>
      <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
        Windows 的 <code>deploy.bat</code> 会自动安装 <code>mikannet://</code> 处理器，无常驻进程。
        上面的按钮仅用于自动安装失效、路径变化或给另一台电脑补装。
      </p>
      <h4 style="margin: 18px 0 8px; color: var(--accent);">网页备用工具</h4>
      <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
        在手机、未安装本机集成的电脑或桌面播放器不可用时，可使用网页播放器和网页文件管理。
        网页播放支持原画 Range 串流及 H.264/AAC 兼容转码。
      </p>
      <div class="row" style="margin-top: 10px;">
        <button class="btn sm" @click="fileBrowserOpen = true">
          <Icon name="folder-open" :size="13" /> 打开网页备用文件管理
        </button>
      </div>
    </div>

    <!-- 数据备份 / 迁移 -->
    <div v-if="activeSection === 'maintenance'" class="card" style="margin-bottom: 12px;">
      <h4 style="margin: 0 0 8px; color: var(--accent);">数据备份 / 迁移</h4>
      <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
        导出<strong>番剧库 / 订阅 / 剧集 / 下载记录 / 本地文件路径 / BD</strong> 为一个 JSON 备份;
        在另一台(如全新部署的)实例<strong>导入</strong>即可迁移历史数据——只要 NAS 文件仍在下载根下的<strong>相同相对路径</strong>,虚拟库即可原样复现。导入为<strong>整表替换</strong>,会覆盖当前数据。
      </p>
      <label class="row" style="cursor: pointer; gap: 6px; font-size: 12.5px; margin: 8px 0;">
        <input type="checkbox" v-model="backupSettings" />
        <span>导出时含设置(API key / 蜜柑 cookie / 偏好;<strong>不含</strong>本机连接与路径,导入时自动还原,无需再勾)</span>
      </label>
      <div class="row" style="gap: 10px; flex-wrap: wrap; align-items: center;">
        <button class="btn sm" @click="exportData"><Icon name="download" :size="13" /> 导出备份</button>
        <input type="file" accept=".json,application/json" @change="onImportFile"
               style="font-size: 12px; max-width: 220px;" />
        <button class="btn sm danger" :disabled="!importFile" @click="importData">
          <Icon name="folder-in" :size="13" /> 导入(覆盖当前数据)
        </button>
        <span class="muted" style="font-size: 12px;">{{ backupMsg }}</span>
      </div>
      <div class="row" style="gap: 10px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
        <button class="btn sm" :disabled="refreshMeta?.running" @click="refetchCovers">
          <Icon name="refresh" :size="13" /> 重新拉取封面 / 元数据
        </button>
        <span class="muted" style="font-size: 12px;">
          <template v-if="refreshMeta?.running">拉取中 {{ refreshMeta.done }}/{{ refreshMeta.total }} … {{ refreshMeta.current }}</template>
          <template v-else-if="refreshMeta">完成:补回封面 {{ refreshMeta.fixed_covers }} 部{{ refreshMeta.errors ? ` · 失败 ${refreshMeta.errors}` : '' }}(刷新页面看封面)</template>
          <template v-else>迁移后封面/banner 没显示?点这个从 bgm.tv/Mikan/TMDB 重新下载(需代理可达)</template>
        </span>
      </div>
    </div>

    <!-- 推送通知 -->
    <h3 v-if="activeSection === 'services'" style="margin: 20px 0 10px; font-size: 15px;">推送通知</h3>
    <div v-for="ch in (activeSection === 'services' ? channels : [])" :key="ch.channel" class="card" style="margin-bottom: 12px;">
      <div class="row" style="margin-bottom: 12px;">
        <strong>{{ channelMeta[ch.channel]?.name ?? ch.channel }}</strong>
        <label class="row" style="cursor: pointer; gap: 6px;">
          <input type="checkbox" v-model="ch.enabled" /> 启用
        </label>
        <label v-if="ch.channel === 'telegram'" class="row" style="cursor: pointer; gap: 6px;">
          <input type="checkbox" v-model="ch.use_proxy" /> 走代理
        </label>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;">{{ testResult[ch.channel] }}</span>
        <button class="btn sm" @click="testCh(ch)">测试推送</button>
        <button class="btn sm primary" :disabled="saving === ch.channel" @click="saveCh(ch)">
          {{ saving === ch.channel ? '保存中…' : '保存' }}
        </button>
      </div>
      <div class="cred-grid">
        <label v-for="[key, label] in channelMeta[ch.channel]?.fields ?? []" :key="key">
          {{ label }}
          <input v-model="ch.credentials[key]" class="input" :placeholder="label"
                 :type="key.includes('token') || key.includes('key') ? 'password' : 'text'" />
        </label>
      </div>
      <div class="row" style="margin-top: 12px; flex-wrap: wrap;">
        <span class="muted" style="font-size: 12.5px;">推送事件:</span>
        <label v-for="(label, ev) in eventLabels" :key="ev" class="row" style="cursor: pointer; gap: 5px;">
          <input type="checkbox" v-model="ch.events[ev]" /> {{ label }}
        </label>
      </div>
    </div>

    <FileBrowserModal :open="fileBrowserOpen" @close="fileBrowserOpen = false" />
  </div>
</template>

<style scoped>
.settings-nav { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin: 0 0 16px; }
.settings-tab { display: flex; align-items: center; gap: 9px; min-width: 0; padding: 10px 11px;
  color: var(--text-dim); background: var(--card); border: 1px solid var(--border);
  border-radius: 9px; cursor: pointer; text-align: left; }
.settings-tab span { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.settings-tab strong { color: var(--text); font-size: 12.5px; white-space: nowrap; }
.settings-tab small { font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.settings-tab:hover { border-color: var(--accent); }
.settings-tab.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--card)); }
.settings-tab.on, .settings-tab.on strong { color: var(--accent); }
.storage-modes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px; }
.storage-modes label { display: flex; justify-content: center; align-items: center; gap: 6px; cursor: pointer;
  border: 1px solid var(--border); border-radius: 8px; padding: 9px; font-size: 12.5px; }
.storage-modes label.on { color: var(--accent); border-color: var(--accent); background: rgba(246,166,35,.07); }
.storage-modes input { display: none; }
.nas-presets { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.nas-presets .muted { font-size: 11.5px; margin-right: 2px; }
.preset { border: 1px solid var(--border); color: var(--text-dim); background: transparent;
  border-radius: 20px; padding: 3px 8px; font-size: 11px; cursor: pointer; }
.preset:hover { color: var(--accent); border-color: var(--accent); }
.cfg-field.full { grid-column: 1 / -1; }
.cfg-field small { font-size: 11px; line-height: 1.5; }
.path-input { display: flex; align-items: center; gap: 7px; min-width: 0; }
.path-input .input { min-width: 0; flex: 1; }
.path-input .btn { flex: none; }
.storage-note { margin: 10px 0 0; color: var(--text-dim); font-size: 11.5px; line-height: 1.6; }
.changelog { white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.6;
  background: var(--bg-soft, rgba(127,127,127,.08)); border-radius: 6px; padding: 8px 10px;
  max-height: 220px; overflow: auto; margin: 0; }
.update-head { flex-wrap: wrap; gap: 8px; }
.cfg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cfg-field { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
.cfg-field.toggle { flex-direction: row; align-items: center; gap: 8px; }
.cfg-field.toggle input { accent-color: var(--accent); }
.cred-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.cred-grid label { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
.load-error {
  display: flex; align-items: center; gap: 10px; color: var(--red);
  margin: -8px 0 14px; font-size: 12.5px;
}
@media (max-width: 768px) {
  .settings-nav { grid-template-columns: 1fr 1fr; }
  .settings-tab:last-child { grid-column: 1 / -1; }
  .storage-modes { grid-template-columns: 1fr; }
  .cfg-grid, .cred-grid { grid-template-columns: 1fr; }
  /* 系统状态:标题独占一行,状态标签/按钮换行,不再把「系统状态」挤成竖排 */
  .health-row { flex-wrap: wrap; }
  .health-row h3 { flex-basis: 100%; }
  .health-row > .spacer { display: none; }
}
</style>
