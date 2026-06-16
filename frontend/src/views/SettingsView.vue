<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'

const health = ref(null)
const cfg = ref({})            // key -> { value, group, type, secret }
const channels = ref([])
const saving = ref('')
const cfgSaved = ref('')
const testResult = ref({})

const LABELS = {
  poll_interval_min: 'RSS 轮询间隔(分钟)',
  tmdb_api_key: 'TMDB API Key',
  downloader: '下载器后端(qb / bitcomet)',
  qb_host: 'qB 地址', qb_port: 'qB 端口', qb_username: 'qB 用户名', qb_password: 'qB 密码',
  download_root: '下载根目录(下载器写盘路径)',
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
  auto_dl_interval_min: '定期智能扫描间隔(分钟,0=关闭)',
  media_host_root: '番剧库宿主机根(你电脑上看到的,如 Z:\\番剧\\mikanarr)',
  bd_owned_host_root: '已购原盘宿主机根(如 Z:\\BD\\已购BD翻录)',
  powerdvd_path: 'PowerDVD.exe 路径(留空 → 自动探测常见安装位)',
}
const GROUP_ORDER = ['常规', '智能下载', '下载器', '代理', '搜索源', '整理', '播放',
  '坏种清理', 'AniDB', 'LLM']

const channelMeta = {
  telegram: { name: 'Telegram Bot', fields: [['bot_token', 'Bot Token'], ['chat_id', 'Chat ID']] },
  serverchan: { name: 'Server酱', fields: [['send_key', 'SendKey (SCT…)']] },
  pushplus: { name: 'PushPlus', fields: [['token', 'Token']] },
}
const eventLabels = { on_new: '检测到更新', on_start: '开始下载', on_complete: '下载完成', on_fail: '下载失败' }

const groups = computed(() => {
  const m = {}
  for (const [key, o] of Object.entries(cfg.value)) {
    (m[o.group] ??= []).push({ key, ...o })
  }
  return GROUP_ORDER.filter(g => m[g]).map(g => ({ group: g, items: m[g] }))
})

async function load() {
  health.value = await api.get('/api/system/health')
  cfg.value = await api.get('/api/config')
  channels.value = await api.get('/api/notifications')
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
  } catch (e) { cfgSaved.value = '保存失败:' + e.message }
}

async function saveCh(ch) {
  saving.value = ch.channel
  try { await api.put(`/api/notifications/${ch.channel}`, ch) } finally { saving.value = '' }
}
async function testCh(ch) {
  testResult.value = { ...testResult.value, [ch.channel]: '发送中…' }
  try {
    await saveCh(ch)
    await api.post(`/api/notifications/${ch.channel}/test`)
    testResult.value = { ...testResult.value, [ch.channel]: '发送成功' }
  } catch (e) { testResult.value = { ...testResult.value, [ch.channel]: '发送失败:' + e.message } }
}
async function pollNow() { await api.post('/api/system/poll') }

// 先保存配置(让 .bat 嵌入当前路径前缀 + 令牌),再下载自安装协议处理器
async function downloadHandler() {
  await saveConfig()
  window.location.href = '/api/launch/handler.bat'
}

// ---- 存储(NAS / SMB,可在此重配;复用首次向导的端点)----
const stor = ref({ mode: 'smb', smb_host_path: '', smb_username: '', smb_password: '', smb_vers: '3.0' })
const storState = ref(null)
const storMsg = ref('')
const storBusy = ref(false)
async function loadStorage() {
  try {
    const s = await api.get('/api/setup/storage')
    storState.value = s
    stor.value.mode = s.mode || 'smb'
    stor.value.smb_host_path = s.smb_host_path || ''
    stor.value.smb_username = s.smb_username || ''
    stor.value.smb_vers = s.smb_vers || '3.0'
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
  storBusy.value = true; storMsg.value = '保存并挂载中…'
  try {
    await api.post('/api/setup/storage', stor.value)
    storMsg.value = '已保存并挂载'
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
  const extra = backupSettings.value ? ',并覆盖设置与通知' : ''
  if (!window.confirm(`导入会用备份覆盖当前的番剧库 / 订阅 / 剧集 / 下载记录 / 文件路径${extra}。确定继续?`)) return
  backupMsg.value = '导入中…'
  try {
    const data = JSON.parse(await importFile.value.text())
    const r = await api.post('/api/backup/import' + (backupSettings.value ? '?include_settings=1' : ''), data)
    backupMsg.value = `导入完成:共写入 ${r.total} 条。封面没显示的话,点下方「重新拉取封面/元数据」。`
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
onUnmounted(() => clearTimeout(refreshTimer))

onMounted(() => { load(); loadStorage() })
</script>

<template>
  <div class="page">
    <div class="page-title">设置</div>

    <div class="card" style="margin-bottom: 16px;">
      <div class="row">
        <h3 style="margin: 0;">系统状态</h3>
        <span class="tag" :class="health?.status === 'ok' ? 'green' : 'red'" v-if="health">
          下载器[{{ health.downloader }}] {{ health.status === 'ok' ? '已连接' : '不可达' }}
        </span>
        <span class="muted" v-if="health?.info"> {{ health.info.version }} </span>
        <div class="spacer" />
        <button class="btn sm" @click="pollNow">立即检查订阅更新</button>
      </div>
    </div>

    <!-- 存储(NAS / SMB;App 在容器内挂载到 /downloads) -->
    <div class="card" style="margin-bottom: 16px;">
      <div class="row" style="margin-bottom: 10px;">
        <h3 style="margin: 0; font-size: 15px;">存储</h3>
        <span v-if="storState" class="tag" :class="storState.mounted ? 'green' : 'red'">
          {{ storState.mounted ? '已挂载' : '未挂载' }}
        </span>
        <span v-if="storState?.error" class="muted" style="font-size: 12px; color: var(--red);">{{ storState.error }}</span>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;">{{ storMsg }}</span>
      </div>
      <div class="row" style="gap: 10px; margin-bottom: 10px;">
        <label class="row" style="gap: 5px; cursor: pointer;"><input type="radio" value="smb" v-model="stor.mode" /> NAS / SMB</label>
        <label class="row" style="gap: 5px; cursor: pointer;"><input type="radio" value="local" v-model="stor.mode" /> 本地 / Docker 路径</label>
      </div>
      <div v-if="stor.mode === 'smb'" class="cfg-grid">
        <label class="cfg-field"><span>共享地址(//主机/共享)</span><input class="input" v-model="stor.smb_host_path" placeholder="//192.168.1.100/anime/mikanarr" /></label>
        <label class="cfg-field"><span>SMB 版本</span><input class="input" v-model="stor.smb_vers" placeholder="3.0" /></label>
        <label class="cfg-field"><span>用户名</span><input class="input" v-model="stor.smb_username" /></label>
        <label class="cfg-field"><span>密码(留空=不改)</span><input class="input" type="password" v-model="stor.smb_password" placeholder="留空保留原密码" /></label>
      </div>
      <p v-else class="muted" style="font-size: 12.5px;">使用容器内 <code>/downloads</code>(由 compose 绑定提供)。</p>
      <div class="row" style="gap: 10px; margin-top: 10px;">
        <button class="btn sm" :disabled="storBusy" @click="testStorage">测试连接</button>
        <button class="btn primary sm" :disabled="storBusy" @click="saveStorage">保存并挂载</button>
        <span class="muted" style="font-size: 12px;">挂载需容器 cap_add: SYS_ADMIN(发行 compose 已带)</span>
      </div>
    </div>

    <!-- 通用配置(DB 覆盖 env,改完即时生效) -->
    <div class="row" style="margin: 8px 0 12px;">
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
          <input v-else-if="it.type === 'int'" type="number" class="input" v-model.number="it.value" />
          <input v-else class="input" v-model="it.value"
                 :type="it.secret ? 'password' : 'text'"
                 :placeholder="it.secret ? '已设置(留空不改)' : ''" />
        </label>
      </div>
    </div>

    <!-- 原生播放:协议处理器 -->
    <div class="card" style="margin-bottom: 12px;">
      <h4 style="margin: 0 0 8px; color: var(--accent);">原生播放 / 协议处理器</h4>
      <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
        详情页的「播放 / 打开目录」「PowerDVD」按钮通过自定义协议
        <code>mikanarr://</code> 在你本机拉起默认播放器 / 资源管理器 / PowerDVD。
        处理器是 Windows 自带的 JScript(经 <code>wscript</code> 运行,<strong>无窗口闪、无需 PowerShell/Python</strong>)。
        用法:先在上方<strong>「播放」</strong>填好宿主机路径前缀,下载下面的安装包<strong>在该 Windows
        电脑上双击运行</strong>一次(certutil 解码 + 注册协议,无常驻进程、无需管理员)。
        首次点击时浏览器会问一次「打开 Mikanarr?」,勾<strong>「始终允许」</strong>后即免提示。
        路径或前缀变更后重新下载安装即可。仅在装了处理器的本机有效,手机/其他设备无效。
      </p>
      <div class="row" style="margin-top: 10px;">
        <button class="btn primary sm" @click="downloadHandler">
          保存配置并下载协议处理器(.bat)
        </button>
        <span class="muted" style="font-size: 12px;">{{ cfgSaved }}</span>
      </div>
    </div>

    <!-- 数据备份 / 迁移 -->
    <div class="card" style="margin-bottom: 12px;">
      <h4 style="margin: 0 0 8px; color: var(--accent);">数据备份 / 迁移</h4>
      <p class="muted" style="font-size: 12.5px; line-height: 1.7;">
        导出<strong>番剧库 / 订阅 / 剧集 / 下载记录 / 本地文件路径 / BD</strong> 为一个 JSON 备份;
        在另一台(如全新部署的)实例<strong>导入</strong>即可迁移历史数据——只要 NAS 文件仍在下载根下的<strong>相同相对路径</strong>,虚拟库即可原样复现。导入为<strong>整表替换</strong>,会覆盖当前数据。
      </p>
      <label class="row" style="cursor: pointer; gap: 6px; font-size: 12.5px; margin: 8px 0;">
        <input type="checkbox" v-model="backupSettings" />
        同时含设置与通知(cookie / 凭据 / 路径前缀,跨机器慎用)
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
    <h3 style="margin: 20px 0 10px; font-size: 15px;">推送通知</h3>
    <div v-for="ch in channels" :key="ch.channel" class="card" style="margin-bottom: 12px;">
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
  </div>
</template>

<style scoped>
.cfg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cfg-field { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
.cfg-field.toggle { flex-direction: row; align-items: center; gap: 8px; }
.cfg-field.toggle input { accent-color: var(--accent); }
.cred-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.cred-grid label { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
@media (max-width: 768px) { .cfg-grid, .cred-grid { grid-template-columns: 1fr; } }
</style>
