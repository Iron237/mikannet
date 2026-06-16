<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'

const STEPS = ['存储', '下载器', '代理', '元数据', '原生播放', '完成']
const step = ref(0)
const busy = ref(false)
const msg = ref('')

// 存储
const st = ref({ mode: 'smb', smb_host_path: '', smb_username: '', smb_password: '', smb_vers: '3.0' })
const stTest = ref(null)       // { ok, error?, sample?, writable? }
// 应用配置(复用 /api/config)
const cfg = ref({
  downloader: 'qb', qb_host: 'host.docker.internal', qb_port: 8080,
  qb_username: 'admin', qb_password: '', download_root: '/downloads',
  proxy_url: '', tmdb_api_key: '', mikan_cookie: '',
  media_host_root: '', bd_owned_host_root: '',
})
const dlHealth = ref('')

onMounted(async () => {
  try {
    const c = await api.get('/api/config')
    for (const k of Object.keys(cfg.value)) if (c[k]) cfg.value[k] = c[k].secret ? '' : c[k].value
    const s = await api.get('/api/setup/storage')
    if (s.mode) st.value.mode = s.mode
    st.value.smb_host_path = s.smb_host_path || ''
    st.value.smb_username = s.smb_username || ''
    st.value.smb_vers = s.smb_vers || '3.0'
  } catch { /* 首启可能尚无配置 */ }
})

async function testStorage() {
  busy.value = true; msg.value = ''; stTest.value = null
  try { stTest.value = await api.post('/api/setup/storage/test', st.value) }
  catch (e) { stTest.value = { ok: false, error: e.message } }
  finally { busy.value = false }
}

async function next() {
  busy.value = true; msg.value = ''
  try {
    if (step.value === 0) {
      // 保存存储并挂载;smb 挂载失败会抛错,挡住下一步
      await api.post('/api/setup/storage', st.value)
    } else if (step.value === 1) {
      await api.put('/api/config', {
        downloader: cfg.value.downloader, qb_host: cfg.value.qb_host, qb_port: cfg.value.qb_port,
        qb_username: cfg.value.qb_username, qb_password: cfg.value.qb_password,
        download_root: cfg.value.download_root,
      })
    } else if (step.value === 2) {
      await api.put('/api/config', { proxy_url: cfg.value.proxy_url })
    } else if (step.value === 3) {
      await api.put('/api/config', { tmdb_api_key: cfg.value.tmdb_api_key, mikan_cookie: cfg.value.mikan_cookie })
    } else if (step.value === 4) {
      await api.put('/api/config', { media_host_root: cfg.value.media_host_root, bd_owned_host_root: cfg.value.bd_owned_host_root })
    }
    step.value++
  } catch (e) { msg.value = e.message } finally { busy.value = false }
}
function back() { msg.value = ''; if (step.value > 0) step.value-- }
function skip() { msg.value = ''; step.value++ }   // 元数据 / 原生播放 可跳过

// 原生播放:先存宿主机路径前缀,再下载协议处理器 .bat(在本机双击运行)
async function downloadHandler() {
  busy.value = true; msg.value = ''
  try {
    await api.put('/api/config', { media_host_root: cfg.value.media_host_root, bd_owned_host_root: cfg.value.bd_owned_host_root })
    window.location.href = '/api/launch/handler.bat'
  } catch (e) { msg.value = e.message } finally { busy.value = false }
}

async function testDownloader() {
  busy.value = true; dlHealth.value = '测试中…'
  try {
    await api.put('/api/config', {
      downloader: cfg.value.downloader, qb_host: cfg.value.qb_host, qb_port: cfg.value.qb_port,
      qb_username: cfg.value.qb_username, qb_password: cfg.value.qb_password,
    })
    const h = await api.get('/api/system/health')
    dlHealth.value = h.status === 'ok'
      ? `连接成功 · ${h.downloader} ${h.info?.version || ''}` : `不可达:${h.error || h.status}`
  } catch (e) { dlHealth.value = '失败:' + e.message } finally { busy.value = false }
}

async function finish() {
  busy.value = true; msg.value = ''
  try { await api.post('/api/setup/finish', {}); window.location.href = '/' }
  catch (e) { msg.value = e.message; busy.value = false }
}
</script>

<template>
  <div class="setup-wrap">
    <div class="setup-card card">
      <div class="brand">🍊 Mikanarr · 首次配置</div>
      <div class="steps">
        <span v-for="(s, i) in STEPS" :key="s" class="step" :class="{ on: i === step, done: i < step }">
          <b>{{ i + 1 }}</b> {{ s }}
        </span>
      </div>

      <!-- 0 存储 -->
      <div v-if="step === 0" class="body">
        <h3>媒体存储</h3>
        <p class="muted">番剧文件存在哪?App 会在容器内挂载它,用于识别/探测/管理(qB 下载路径在下一步单独配)。</p>
        <div class="row seg">
          <label :class="{ on: st.mode === 'smb' }"><input type="radio" value="smb" v-model="st.mode" /> NAS / SMB 共享</label>
          <label :class="{ on: st.mode === 'local' }"><input type="radio" value="local" v-model="st.mode" /> 本地 / Docker 路径</label>
        </div>
        <template v-if="st.mode === 'smb'">
          <label class="fld"><span>共享地址</span><input class="input" v-model="st.smb_host_path" placeholder="//192.168.1.100/anime/mikanarr" /></label>
          <div class="grid2">
            <label class="fld"><span>用户名</span><input class="input" v-model="st.smb_username" placeholder="NAS 账号" /></label>
            <label class="fld"><span>密码</span><input class="input" type="password" v-model="st.smb_password" placeholder="NAS 密码" /></label>
          </div>
          <label class="fld" style="max-width:160px;"><span>SMB 版本</span><input class="input" v-model="st.smb_vers" placeholder="3.0" /></label>
        </template>
        <p v-else class="muted">将使用容器内 <code>/downloads</code>(由 compose 默认绑定的本地目录提供)。</p>
        <div class="row" style="gap:10px;">
          <button class="btn" :disabled="busy" @click="testStorage"><Icon name="check" :size="13" /> 测试连接</button>
          <span v-if="stTest" :class="stTest.ok ? 'ok' : 'err'" style="font-size:12.5px;">
            <template v-if="stTest.ok">可连接{{ stTest.writable === false ? '(只读!写入失败)' : '·可写' }};样例:{{ (stTest.sample || []).slice(0, 3).join(' / ') || '(空)' }}</template>
            <template v-else>{{ stTest.error }}</template>
          </span>
        </div>
      </div>

      <!-- 1 下载器 -->
      <div v-else-if="step === 1" class="body">
        <h3>下载器</h3>
        <p class="muted">连接 qBittorrent(桌面版推荐 <code>host.docker.internal</code> + 桌面端口)。可「测试」,也可先跳过稍后在设置页配。</p>
        <div class="grid2">
          <label class="fld"><span>后端</span>
            <select class="input" v-model="cfg.downloader"><option value="qb">qBittorrent</option><option value="bitcomet">BitComet</option></select>
          </label>
          <label class="fld"><span>下载写盘根(qB 视角)</span><input class="input" v-model="cfg.download_root" placeholder="/downloads 或 NAS 路径" /></label>
          <label class="fld"><span>地址</span><input class="input" v-model="cfg.qb_host" /></label>
          <label class="fld"><span>端口</span><input class="input" type="number" v-model.number="cfg.qb_port" /></label>
          <label class="fld"><span>用户名</span><input class="input" v-model="cfg.qb_username" /></label>
          <label class="fld"><span>密码</span><input class="input" type="password" v-model="cfg.qb_password" /></label>
        </div>
        <div class="row" style="gap:10px;">
          <button class="btn" :disabled="busy" @click="testDownloader">测试连接</button>
          <span class="muted" style="font-size:12.5px;">{{ dlHealth }}</span>
        </div>
      </div>

      <!-- 2 代理 -->
      <div v-else-if="step === 2" class="body">
        <h3>代理</h3>
        <p class="muted">国内访问蜜柑 / bgm.tv / TMDB / Telegram 基本都需要代理;容器内访问宿主代理用 <code>host.docker.internal</code>。不用代理可留空。</p>
        <label class="fld"><span>代理地址</span><input class="input" v-model="cfg.proxy_url" placeholder="http://host.docker.internal:10808" /></label>
      </div>

      <!-- 3 元数据(可跳) -->
      <div v-else-if="step === 3" class="body">
        <h3>元数据(可跳过)</h3>
        <p class="muted">TMDB key 用于横版背景图;蜜柑 cookie 用于「批量导入我的番组」。都可留空,稍后在设置页补。</p>
        <label class="fld"><span>TMDB API Key</span><input class="input" v-model="cfg.tmdb_api_key" placeholder="可留空" /></label>
        <label class="fld"><span>蜜柑 Cookie</span><textarea class="input" rows="2" v-model="cfg.mikan_cookie" placeholder="可留空"></textarea></label>
      </div>

      <!-- 4 原生播放(可跳) -->
      <div v-else-if="step === 4" class="body">
        <h3>原生播放(可跳过)</h3>
        <p class="muted">想用本机默认播放器播放、在资源管理器打开、PowerDVD 放蓝光?填这台电脑看 NAS 的<strong>路径前缀</strong>,
          再下载协议处理器在本机双击装一次。不需要可跳过,以后在设置页随时配。</p>
        <label class="fld"><span>番剧库宿主机根(如 Z:\番剧\mikanarr)</span><input class="input" v-model="cfg.media_host_root" placeholder="Z:\番剧\mikanarr" /></label>
        <label class="fld"><span>已购原盘宿主机根(可选,如 Z:\BD\已购BD翻录)</span><input class="input" v-model="cfg.bd_owned_host_root" placeholder="可留空" /></label>
        <div class="row" style="gap:10px;">
          <button class="btn" :disabled="busy || !cfg.media_host_root" @click="downloadHandler">保存并下载协议处理器(.bat)</button>
          <span class="muted" style="font-size:12px;">下载后在这台 Windows 双击运行一次</span>
        </div>
      </div>

      <!-- 5 完成 -->
      <div v-else class="body">
        <h3>完成 🎉</h3>
        <p class="muted">配置已保存。点「进入 Mikanarr」开始用——搜索番剧、建订阅、导入历史数据(设置页「数据备份/迁移」)。</p>
      </div>

      <p v-if="msg" class="err" style="font-size:12.5px;">{{ msg }}</p>
      <div class="row foot">
        <button class="btn" v-if="step > 0 && step < 5" @click="back">上一步</button>
        <div class="spacer" />
        <button class="btn" v-if="step === 3 || step === 4" :disabled="busy" @click="skip">跳过</button>
        <button class="btn primary" v-if="step < 5" :disabled="busy" @click="next">
          {{ busy ? '处理中…' : '下一步' }}
        </button>
        <button class="btn primary" v-else :disabled="busy" @click="finish">
          {{ busy ? '进入中…' : '进入 Mikanarr' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
.setup-card { width: 100%; max-width: 560px; padding: 26px 28px; }
.brand { font-size: 18px; font-weight: 800; letter-spacing: .5px; margin-bottom: 16px; }
.steps { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 18px; }
.step { font-size: 12px; color: var(--text-dim); display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 20px; border: 1px solid var(--border); }
.step b { background: var(--border); color: var(--text); border-radius: 50%; width: 16px; height: 16px;
  display: inline-flex; align-items: center; justify-content: center; font-size: 10px; }
.step.on { color: var(--accent); border-color: var(--accent); }
.step.on b { background: var(--accent); color: #1a1207; }
.step.done { color: var(--green); border-color: var(--green); }
.step.done b { background: var(--green); color: #07210f; }
.body h3 { margin: 0 0 6px; font-size: 16px; }
.body .muted { font-size: 12.5px; line-height: 1.6; margin-bottom: 14px; }
.seg { gap: 10px; margin-bottom: 14px; }
.seg label { flex: 1; text-align: center; padding: 9px; border: 1px solid var(--border); border-radius: 8px;
  cursor: pointer; font-size: 13px; }
.seg label.on { border-color: var(--accent); color: var(--accent); }
.seg input { display: none; }
.fld { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--text-dim); margin-bottom: 12px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.foot { margin-top: 20px; }
.ok { color: var(--green); }
.err { color: var(--red); }
@media (max-width: 560px) { .grid2 { grid-template-columns: 1fr; } }
</style>
