<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, fmtSize } from '../api'
import Icon from './Icon.vue'

const props = defineProps({
  bangumi: { type: Object, required: true },
})
const emit = defineEmits(['add-subscription', 'change-source', 'changed'])

const strategy = ref(null)
const loading = ref(true)
const busy = ref('')
const msg = ref('')
const selectedNumber = ref(null)
const history = ref([])
let timer = null
let mounted = true

const coverage = computed(() => strategy.value?.coverage || {})
const policy = computed(() => strategy.value?.policy || {})
const auto = computed(() => strategy.value?.auto || {})
const modeNames = {
  fill_upgrade: '补全并升级 BD',
  fill_only: '仅补全缺集',
  review: '只生成建议',
}
const selectedEpisode = computed(() =>
  coverage.value.episodes?.find(ep => ep.number === selectedNumber.value) || null)
const bdLabel = computed(() => {
  const c = coverage.value
  if (c.bd_status === 'complete') return `BD 完整 ${c.bd.length}/${c.total}`
  if (c.bd_status === 'partial') return `BD 部分覆盖 ${c.bd.length}/${c.total || '?'}`
  if (c.bd_status === 'active') return `BD 资源已生效`
  if (c.bd_status === 'release_only') return `仅检测到 ${c.bd_release_count} 套 BD 原盘`
  return '无 BD 正片'
})

function fmtTime(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString('zh-CN') } catch { return iso }
}
function fmtRange(nums) {
  if (!nums?.length) return '无'
  const a = [...nums].sort((x, y) => x - y)
  const out = []
  let s = a[0], p = a[0]
  for (const n of a.slice(1).concat([NaN])) {
    if (n === p + 1) { p = n; continue }
    out.push(s === p ? `${s}` : `${s}-${p}`)
    s = p = n
  }
  return out.join(', ')
}
function sourceClass(ep) {
  return ep.active_source === 'BD' ? 'bd' : ep.active_source === 'Web' ? 'web'
    : ep.active_source ? 'unknown' : 'missing'
}

async function load() {
  try {
    const [resource, audit] = await Promise.all([
      api.get(`/api/bangumi/${props.bangumi.id}/resource-strategy`),
      api.get(`/api/bangumi/${props.bangumi.id}/auto-history?limit=5`),
    ])
    strategy.value = resource
    history.value = audit
  } catch (e) { msg.value = e.message }
  finally { loading.value = false }
}
async function refreshAfter(message = '') {
  if (message) msg.value = message
  await load()
  emit('changed')
}
async function syncNow() {
  busy.value = 'sync'; msg.value = ''
  try {
    const r = await api.post(`/api/bangumi/${props.bangumi.id}/sync-resources`, {})
    msg.value = `订阅已检查 ${r.rss_checked} 个`
      + (r.auto_started ? ',自动补全扫描已启动' : r.auto_note ? `,${r.auto_note}` : '')
    if (r.auto_started) pollAuto()
    else await refreshAfter()
  } catch (e) { msg.value = e.message }
  finally { busy.value = '' }
}
async function pollAuto() {
  if (!mounted) return
  await load()
  if (auto.value.scanning) timer = setTimeout(pollAuto, 1500)
  else emit('changed')
}
async function toggleAuto(e) {
  const enabled = e.target.checked
  busy.value = 'auto'; msg.value = ''
  try {
    await api.patch(`/api/bangumi/${props.bangumi.id}`, { auto_best: enabled })
    if (enabled) {
      await api.post(`/api/bangumi/${props.bangumi.id}/auto-scan`, {})
      msg.value = '已开启常驻策略,并立即开始第一次扫描'
      pollAuto()
    } else {
      await refreshAfter('已关闭定期自动补全;现有任务不受影响')
    }
  } catch (e) {
    msg.value = e.message
    await load()
  } finally { busy.value = '' }
}
async function togglePolicy(field, checked) {
  busy.value = field; msg.value = ''
  try {
    await api.patch(`/api/bangumi/${props.bangumi.id}`, { [field]: checked })
    await refreshAfter(field === 'auto_download_disabled'
      ? (checked ? '已停止该番剧的自动获取' : '已恢复自动获取')
      : '收藏状态已更新')
  } catch (e) { msg.value = e.message; await load() }
  finally { busy.value = '' }
}
async function changeAutoMode(e) {
  const mode = e.target.value
  busy.value = 'auto-mode'; msg.value = ''
  try {
    await api.patch(`/api/bangumi/${props.bangumi.id}`, { auto_mode: mode })
    await refreshAfter(`自动扫描方式已改为“${modeNames[mode]}”`)
  } catch (err) { msg.value = err.message; await load() }
  finally { busy.value = '' }
}
async function approveReview(logId) {
  busy.value = `approve-${logId}`; msg.value = ''
  try {
    const r = await api.post(
      `/api/bangumi/${props.bangumi.id}/auto-history/${logId}/approve`, {})
    await refreshAfter(`已确认并提交 ${r.submitted} 个种子`)
  } catch (e) { msg.value = e.message }
  finally { busy.value = '' }
}
async function pollSubscription(sub) {
  busy.value = `sub-${sub.id}`; msg.value = ''
  try {
    const r = await api.post(`/api/subscriptions/${sub.id}/poll`, {})
    await refreshAfter(r.error ? `检查失败:${r.error}`
      : `已检查 ${sub.subgroup_name || sub.mikan_subgroup_id}:接受 ${r.accepted ?? 0},恢复 ${r.revived ?? 0}`)
  } catch (e) { msg.value = e.message }
  finally { busy.value = '' }
}
async function scanLibrary() {
  busy.value = 'library'; msg.value = ''
  try {
    await api.post('/api/import/library-scan', {})
    msg.value = '正在扫描番剧库…'
    pollLibrary()
  } catch (e) { msg.value = e.message; busy.value = '' }
}
async function pollLibrary() {
  if (!mounted) return
  try {
    const state = await api.get('/api/import/library-scan/status')
    if (state.running) {
      msg.value = `正在扫描番剧库 ${state.done}/${state.total}${state.current ? ` · ${state.current}` : ''}`
      timer = setTimeout(pollLibrary, 1500)
    } else {
      busy.value = ''
      await refreshAfter(`番剧库扫描完成:新增 ${state.registered || 0},更新 ${state.updated || 0}`)
    }
  } catch (e) { msg.value = e.message; busy.value = '' }
}
async function activateVersion(version, preferred = true) {
  busy.value = `file-${version.id}`; msg.value = ''
  try {
    await api.post(`/api/files/${version.id}/activate`, { preferred })
    await refreshAfter(preferred
      ? `第 ${selectedNumber.value} 话已切换为 ${version.source} 版本`
      : `第 ${selectedNumber.value} 话已恢复自动选择`)
  } catch (e) { msg.value = e.message }
  finally { busy.value = '' }
}

defineExpose({ load })
onMounted(load)
watch(() => props.bangumi.id, () => { loading.value = true; load() })
onUnmounted(() => { mounted = false; clearTimeout(timer) })
</script>

<template>
  <section class="resource card">
    <div class="row heading">
      <div>
        <h2><Icon name="database" :size="17" /> 资源策略</h2>
        <p class="muted">统一管理订阅追更、缺集补全和 Web → BD 升级</p>
      </div>
      <div class="spacer" />
      <button class="btn primary" :disabled="busy === 'sync' || policy.stop_automatic"
              @click="syncNow">
        <Icon name="refresh" :size="14" /> {{ busy === 'sync' ? '同步中…' : '立即同步' }}
      </button>
    </div>

    <div v-if="loading" class="muted loading">加载资源状态…</div>
    <template v-else-if="strategy">
      <div class="summary-grid">
        <div class="metric" :class="coverage.bd_status">
          <span class="label">BD</span><strong>{{ bdLabel }}</strong>
        </div>
        <div class="metric">
          <span class="label">Web 生效</span><strong>{{ coverage.web?.length || 0 }}{{ coverage.total ? `/${coverage.total}` : '' }}</strong>
        </div>
        <div class="metric" :class="{ warn: coverage.missing?.length }">
          <span class="label">缺失</span><strong>{{ coverage.missing?.length || 0 }}</strong>
        </div>
        <div class="metric">
          <span class="label">备用版本</span><strong>{{ coverage.fallback_count || 0 }} 集</strong>
        </div>
      </div>

      <div v-if="coverage.episodes?.length" class="coverage">
        <div class="coverage-head">
          <span>逐集覆盖</span>
          <span class="legend"><i class="bd" />BD <i class="web" />Web <i class="missing" />缺失</span>
        </div>
        <div class="episode-map">
          <span v-for="ep in coverage.episodes" :key="ep.number" class="ep"
                :class="sourceClass(ep)"
                @click="selectedNumber = selectedNumber === ep.number ? null : ep.number"
                :title="`第 ${ep.number} 话:${ep.active_source || '缺失'}`
                  + (ep.fallback_sources?.length ? `;备用 ${ep.fallback_sources.join('/')}` : '')">
            {{ ep.number }}
          </span>
        </div>
        <div v-if="selectedEpisode?.versions?.length" class="versions">
          <div class="row version-head">
            <strong>第 {{ selectedEpisode.number }} 话版本</strong>
            <span class="muted">手动选择会覆盖自动 BD &gt; Web 判优</span>
            <div class="spacer" />
            <button v-if="selectedEpisode.versions.some(v => v.preferred)" class="btn xs"
                    :disabled="!!busy"
                    @click="activateVersion(selectedEpisode.versions.find(v => v.active), false)">
              恢复自动选择
            </button>
          </div>
          <div v-for="v in selectedEpisode.versions" :key="v.id" class="version-row">
            <span class="tag" :class="v.source === 'BD' ? 'accent' : 'blue'">{{ v.source }}</span>
            <span v-if="v.active" class="tag green">当前</span>
            <span v-else-if="!v.exists" class="tag red">文件不存在</span>
            <span v-if="v.preferred" class="tag">手动选择</span>
            <span class="version-name" :title="v.name">{{ v.name }}</span>
            <span class="muted">{{ fmtSize(v.size) }}</span>
            <div class="spacer" />
            <button v-if="!v.active && v.exists" class="btn xs" :disabled="!!busy"
                    @click="activateVersion(v)">
              {{ busy === `file-${v.id}` ? '切换中…' : '设为当前' }}
            </button>
          </div>
        </div>
        <p v-if="coverage.missing?.length" class="hint warn-text">缺 {{ fmtRange(coverage.missing) }}</p>
        <p v-if="coverage.cleanup_blocked_torrents" class="hint">
          <Icon name="alert" :size="12" /> {{ coverage.cleanup_blocked_torrents }}
          个合集同时含备用 Web 与生效文件,为保证种子完整性暂不自动清理。
        </p>
      </div>

      <div class="policy-grid">
        <label :class="{ disabled: busy }">
          <input type="checkbox" :checked="auto.enabled" :disabled="!!busy || policy.stop_automatic"
                 @change="toggleAuto" />
          <span><b>常驻智能扫描</b>
            <small>每 {{ policy.interval_minutes }} 分钟 · {{ policy.resolution }} · {{ policy.subtitle_language }}</small></span>
        </label>
        <div class="mode-policy">
          <span><b>自动扫描方式</b><small>决定定时扫描与“立即同步”的实际动作</small></span>
          <select class="input" :value="auto.mode" :disabled="!!busy"
                  @change="changeAutoMode">
            <option value="fill_upgrade">补全缺集并升级 Web→BD</option>
            <option value="fill_only">仅补全缺集</option>
            <option value="review">只生成建议，确认后下载</option>
          </select>
        </div>
        <label>
          <input type="checkbox" :checked="policy.owned" :disabled="!!busy"
                 @change="togglePolicy('bd_owned', $event.target.checked)" />
          <span><b>已拥有原盘</b><small>仅收藏标记,不会改变下载策略</small></span>
        </label>
        <label class="stop">
          <input type="checkbox" :checked="policy.stop_automatic" :disabled="!!busy"
                 @change="togglePolicy('auto_download_disabled', $event.target.checked)" />
          <span><b>停止自动获取</b><small>暂停订阅轮询和自动补全,手动导入仍可用</small></span>
        </label>
      </div>

      <div class="auto-audit">
        <span :class="['dot', auto.scanning ? 'running' : auto.enabled ? 'on' : '']" />
        <b>{{ auto.scanning ? '正在扫描' : auto.enabled ? '常驻已开启' : '仅手动' }}</b>
        <span class="tag">{{ modeNames[auto.mode] || auto.mode }}</span>
        <span class="muted">上次扫描 {{ fmtTime(auto.last_scan_at) }}</span>
        <span v-if="!auto.last_scan_at && auto.last_activity_at" class="muted">
          历史任务活动 {{ fmtTime(auto.last_activity_at) }}
        </span>
        <span v-if="auto.next_run_at && auto.enabled" class="muted">下次 {{ fmtTime(auto.next_run_at) }}</span>
        <span v-if="auto.last_result?.note" class="muted">结果:{{ auto.last_result.note }}</span>
        <button v-if="auto.pending_review" class="btn xs"
                :disabled="!!busy"
                @click="approveReview(auto.pending_review_log_id)">
          {{ busy === `approve-${auto.pending_review_log_id}` ? '提交中…' : `确认下载 ${auto.pending_review} 个建议` }}
        </button>
      </div>

      <div v-if="history.length" class="history">
        <div class="history-head"><strong>最近扫描记录</strong><span class="muted">保留每次选择依据与结果</span></div>
        <div v-for="row in history" :key="row.id" class="history-row">
          <span>{{ fmtTime(row.created_at) }}</span>
          <span class="tag">{{ modeNames[row.mode] || row.mode }}</span>
          <span class="muted">{{ row.trigger === 'scheduled' ? '定时' : row.trigger === 'sync' ? '立即同步' : '手动' }}</span>
          <span v-if="row.result.error" class="error-text">失败:{{ row.result.error }}</span>
          <span v-else-if="row.pending" class="muted">待确认 {{ row.pending }} 个</span>
          <span v-else class="muted">候选 {{ row.result.candidates || 0 }} · 提交 {{ row.result.submitted || 0 }}</span>
          <div class="spacer" />
          <button v-if="row.pending" class="btn xs" :disabled="!!busy"
                  @click="approveReview(row.id)">
            {{ busy === `approve-${row.id}` ? '提交中…' : '确认下载' }}
          </button>
        </div>
      </div>

      <div class="sources">
        <div class="row source-head">
          <strong>固定订阅源</strong><span class="tag">{{ strategy.subscriptions.length }}</span>
          <div class="spacer" />
          <button class="btn sm" @click="emit('add-subscription')"><Icon name="plus" :size="13" /> 添加订阅</button>
        </div>
        <div v-if="!strategy.subscriptions.length" class="muted empty-source">
          没有固定字幕组;仍可使用跨字幕组自动补全。
        </div>
        <div v-for="s in strategy.subscriptions" :key="s.id" class="source-row">
          <span class="health" :class="s.last_poll_ok ? 'ok' : 'bad'" />
          <strong>{{ s.subgroup_name || s.mikan_subgroup_id }}</strong>
          <span class="tag" :class="s.enabled ? 'green' : ''">{{ s.enabled ? '追更中' : '已停用' }}</span>
          <div class="spacer" />
          <button class="btn xs" :disabled="!!busy || !s.enabled || policy.stop_automatic"
                  @click="pollSubscription(s)">
            <Icon name="refresh" :size="12" /> {{ busy === `sub-${s.id}` ? '检查中…' : '立即检查' }}
          </button>
          <button class="btn xs" @click="emit('change-source', s)">
            <Icon name="refresh" :size="12" /> 更换来源
          </button>
        </div>
      </div>

      <div class="row tools">
        <span class="muted">本地资源</span>
        <button class="btn sm" :disabled="busy === 'library'" @click="scanLibrary">
          <Icon name="scan" :size="13" /> 扫描番剧库
        </button>
        <RouterLink class="btn sm" to="/bd"><Icon name="disc" :size="13" /> BD 收藏与导入</RouterLink>
      </div>
      <p v-if="msg" class="message">{{ msg }}</p>
    </template>
  </section>
</template>

<style scoped>
.resource { padding: 18px; margin-bottom: 20px; border-color: var(--accent-dim); }
.heading h2 { display: flex; align-items: center; gap: 7px; font-size: 17px; margin: 0; }
.heading p { font-size: 12px; margin-top: 3px; }
.loading { padding: 24px 0; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
.metric { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex;
  flex-direction: column; gap: 3px; min-width: 0; }
.metric .label { color: var(--text-dim); font-size: 11.5px; }
.metric strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.metric.complete { border-color: var(--green); }
.metric.partial, .metric.warn { border-color: var(--accent-dim); }
.coverage { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 12px; }
.coverage-head { display: flex; justify-content: space-between; font-size: 12.5px; margin-bottom: 7px; }
.legend { color: var(--text-dim); display: flex; gap: 5px; align-items: center; font-size: 11px; }
.legend i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; margin-left: 4px; }
.legend .bd, .ep.bd { background: color-mix(in srgb, var(--accent) 72%, #40320a); }
.legend .web, .ep.web { background: #315e8a; }
.legend .missing, .ep.missing { background: transparent; border-color: var(--red); color: var(--red); }
.episode-map { display: flex; gap: 5px; flex-wrap: wrap; }
.ep { width: 28px; height: 25px; border: 1px solid transparent; border-radius: 6px; display: inline-flex;
  align-items: center; justify-content: center; font-size: 11px; color: #fff; cursor: pointer; }
.ep:hover { outline: 2px solid color-mix(in srgb, var(--accent) 55%, transparent); }
.ep.unknown { background: var(--text-dim); }
.versions { margin-top: 9px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.version-head { font-size: 11.5px; margin-bottom: 5px; gap: 7px; }
.version-row { display: flex; align-items: center; gap: 6px; padding: 5px 0; font-size: 11.5px; }
.version-name { min-width: 0; max-width: 46%; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.hint { margin-top: 7px; font-size: 11.5px; color: var(--text-dim); display: flex; gap: 5px; align-items: center; }
.warn-text { color: var(--red); }
.policy-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
.policy-grid label { display: flex; gap: 9px; align-items: flex-start; padding: 10px;
  border: 1px solid var(--border); border-radius: 8px; cursor: pointer; }
.mode-policy { display: flex; gap: 8px; align-items: center; justify-content: space-between;
  padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; }
.mode-policy > span { min-width: 0; }
.mode-policy .input { max-width: 220px; font-size: 11.5px; }
.policy-grid label.stop { border-color: color-mix(in srgb, var(--red) 35%, var(--border)); }
.policy-grid input { margin-top: 3px; accent-color: var(--accent); }
.policy-grid span { display: flex; flex-direction: column; gap: 3px; font-size: 12.5px; }
.policy-grid small { color: var(--text-dim); line-height: 1.35; }
.auto-audit { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px;
  padding: 8px 10px; background: var(--bg-hover); border-radius: 8px; font-size: 11.5px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-dim); }
.dot.on { background: var(--green); }.dot.running { background: var(--accent); box-shadow: 0 0 7px var(--accent); }
.history { margin-top: 8px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.history-head { display: flex; gap: 8px; align-items: baseline; font-size: 11.5px; margin-bottom: 3px; }
.history-row { display: flex; align-items: center; gap: 6px; padding: 5px 0; font-size: 11.5px;
  border-top: 1px solid var(--border); }
.error-text { color: var(--red); }
.sources { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.source-head { margin-bottom: 6px; }
.source-row { display: flex; align-items: center; gap: 7px; padding: 7px 2px; font-size: 12.5px; }
.health { width: 8px; height: 8px; border-radius: 50%; background: var(--text-dim); }
.health.ok { background: var(--green); }.health.bad { background: var(--red); }
.empty-source { font-size: 12px; padding: 7px 0; }
.tools { margin-top: 10px; gap: 7px; flex-wrap: wrap; }
.message { margin-top: 10px; font-size: 12px; color: var(--accent); }
.btn.xs { font-size: 11.5px; padding: 3px 8px; }
@media (max-width: 800px) {
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .policy-grid { grid-template-columns: 1fr; }
  .mode-policy { align-items: flex-start; flex-direction: column; }
  .mode-policy .input { max-width: none; width: 100%; }
  .history-row { flex-wrap: wrap; }
  .source-row { flex-wrap: wrap; }
}
</style>
