<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

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
  dead_torrent_enabled: '自动清理坏种(无做种且卡住)',
  dead_torrent_hours: '坏种判定:卡住超过几小时',
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
}
const GROUP_ORDER = ['常规', '智能下载', '下载器', '代理', '搜索源', '整理', '坏种清理', 'AniDB', 'LLM']

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
    const payload = Object.fromEntries(Object.entries(cfg.value).map(([k, o]) => [k, o.value]))
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

onMounted(load)
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
