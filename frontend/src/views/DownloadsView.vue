<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, fmtSize, fmtSpeed } from '../api'
import { useTasksStore } from '../stores/tasks'
import Icon from '../components/Icon.vue'

const store = useTasksStore()
const delConfirm = ref(null)      // { ids: [...] } 待确认删除(单条或批量)
const attentionOnly = ref(false)
onMounted(() => store.refresh())
const selected = ref(new Set())
const busy = ref(false)
const opMsg = ref('')

const statusLabel = {
  pending: ['等待提交', 'blue'], downloading: ['下载中', 'accent'],
  completed: ['待入库', 'blue'], archived: ['已入库', 'green'],
  submit_failed: ['提交失败', 'red'], download_error: ['下载错误', 'red'],
  skipped: ['已跳过', ''],
}

const visibleActive = computed(() => attentionOnly.value
  ? store.needsAttention.filter(t => t.status === 'completed') : store.active)
const visibleHistory = computed(() => attentionOnly.value
  ? store.needsAttention.filter(t => t.status !== 'completed') : store.history)

// 进行中任务按番剧与季分组
const groups = computed(() => {
  const m = new Map()
  for (const t of visibleActive.value) {
    const key = (t.bangumi_title || '未分组') + ' · 第 ' + (t.season_number ?? 1) + ' 季'
    if (!m.has(key)) m.set(key, [])
    m.get(key).push(t)
  }
  return [...m.entries()].map(([title, items]) => ({ title, items }))
})
const allIds = computed(() => [...visibleActive.value, ...visibleHistory.value].map(t => t.id))
const allSelected = computed(() => allIds.value.length > 0 && selected.value.size === allIds.value.length)
const selectedTasks = computed(() =>
  [...visibleActive.value, ...visibleHistory.value].filter(t => selected.value.has(t.id)))
const canPause = computed(() =>
  selectedTasks.value.some(t => t.status === 'downloading' && !t.paused))
const canRecover = computed(() =>
  selectedTasks.value.some(t => t.paused
    || ['completed', 'download_error', 'submit_failed'].includes(t.status)))

function isSel(id) { return selected.value.has(id) }
function toggle(id) {
  const s = new Set(selected.value); s.has(id) ? s.delete(id) : s.add(id); selected.value = s
}
function selectAll() { selected.value = new Set(allIds.value) }
function clearSel() { selected.value = new Set() }

watch(allIds, ids => {
  const visible = new Set(ids)
  for (const id of selected.value) if (!visible.has(id)) selected.value.delete(id)
})

function fmtEta(s) {
  if (s == null || s < 0 || s >= 8640000) return '∞'
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h${m}m` : (m ? `${m}m` : `${s}s`)
}

function statusText(t) {
  if (t.status === 'downloading' && t.paused) return ['已暂停', '']
  return statusLabel[t.status] || [t.status, '']
}

async function act(task, action) {
  opMsg.value = ''
  try {
    await api.post(`/api/tasks/${task.id}/${action}`)
    await store.refresh()
  } catch (e) { opMsg.value = e.message }
}

async function batchAct(action) {
  if (action === 'delete') { askDelete([...selected.value]); return }
  opMsg.value = ''
  busy.value = true
  try {
    const r = await api.post('/api/tasks/batch', { ids: [...selected.value], action })
    selected.value = new Set(r.failed)
    if (r.failed.length) opMsg.value = `已处理 ${r.done.length} 项，另有 ${r.failed.length} 项未成功，已保留选择。`
    await store.refresh()
  } catch (e) { opMsg.value = e.message }
  finally { busy.value = false }
}
function askDelete(ids) { delConfirm.value = { ids, deleteFiles: false } }
function closeDelete() { if (!busy.value) delConfirm.value = null }
async function doDelete() {
  busy.value = true
  opMsg.value = ''
  try {
    const { ids, deleteFiles } = delConfirm.value
    const r = await api.post('/api/tasks/batch', { ids, action: 'delete', delete_files: deleteFiles })
    delConfirm.value = null
    selected.value = new Set(r.failed)
    if (r.failed.length) opMsg.value = `已删除 ${r.done.length} 项，另有 ${r.failed.length} 项删除失败，已保留选择。`
    await store.refresh()
  } catch (e) { opMsg.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 10px;">
      <div class="page-title" style="margin: 0;">下载任务</div>
      <button type="button" class="btn sm" :class="{ primary: attentionOnly }"
              :aria-pressed="attentionOnly" @click="attentionOnly = !attentionOnly">
        需处理 {{ store.needsAttention.length }}
      </button>
      <div class="spacer" />
      <button type="button" class="btn sm" :disabled="store.loading" @click="store.refresh()">
        <Icon name="refresh" :size="13" /> 重新同步
      </button>
      <button class="btn sm" :disabled="!allIds.length" @click="allSelected ? clearSel() : selectAll()">
        {{ allSelected ? '取消全选' : '全选' }}
      </button>
      <button class="btn sm" :disabled="!selected.size" @click="clearSel">清空选择</button>
    </div>

    <div v-if="selected.size" class="batch-bar card">
      <strong>已选 {{ selected.size }} 项</strong>
      <div class="spacer" />
      <button class="btn sm" :disabled="busy || !canPause" @click="batchAct('pause')">
        <Icon name="pause" :size="13" /> 暂停下载
      </button>
      <button class="btn sm" :disabled="busy || !canRecover" @click="batchAct('retry')">
        <Icon name="refresh" :size="13" /> 恢复 / 重试
      </button>
      <button class="btn sm danger" :disabled="busy" @click="batchAct('delete')"><Icon name="trash" :size="13" /> 删除</button>
    </div>

    <div role="status" class="sync-status">
      <p v-if="store.syncError">任务同步失败 · {{ store.syncError }}<span v-if="store.lastSync"> · 上次同步 {{ new Date(store.lastSync).toLocaleTimeString() }}</span></p>
      <p v-else-if="!store.lastSync">正在读取下载任务…</p>
      <p v-else-if="!store.wsConnected">实时连接已断开，正在重连。上次同步 {{ new Date(store.lastSync).toLocaleTimeString() }}</p>
      <p v-else-if="store.liveAvailable === false">下载跟踪暂不可用，当前显示已保存的任务。</p>
    </div>

    <p v-if="opMsg" role="status" class="op-msg">{{ opMsg }}</p>

    <p v-if="attentionOnly && store.lastSync && !store.syncError && !allIds.length" class="muted">暂无需处理的任务</p>
    <div v-if="!attentionOnly && store.lastSync && !store.syncError && !store.active.length" class="muted" style="margin: 10px 0 24px;">没有进行中的任务</div>

    <div v-for="g in groups" :key="g.title" class="grp">
      <h3 class="grp-title">{{ g.title }} <span class="muted">({{ g.items.length }})</span></h3>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th class="ck-col"></th><th class="name-col">名称</th><th>进度</th><th>状态</th>
              <th>大小</th><th>↓速</th><th>↑速</th><th>剩余时间</th><th>做种/下载</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in g.items" :key="t.id" :class="{ sel: isSel(t.id) }">
              <td><input type="checkbox" class="ck" :checked="isSel(t.id)" @change="toggle(t.id)" /></td>
              <td class="name-col" :title="t.title_raw">{{ t.title_raw }}<div v-if="t.error_message" class="task-error">{{ t.error_message }}</div></td>
              <td class="prog">
                <div class="bar"><div class="fill" :style="{ width: (t.progress * 100).toFixed(0) + '%' }" /></div>
                <span>{{ (t.progress * 100).toFixed(0) }}%</span>
              </td>
              <td>
                <span class="tag" :class="statusText(t)[1]" :title="t.state || ''">{{ statusText(t)[0] }}</span>
              </td>
              <td class="num">{{ fmtSize(t.size) }}</td>
              <td class="num">{{ t.status === 'downloading' ? fmtSpeed(t.dlspeed) : '—' }}</td>
              <td class="num">{{ t.upspeed ? fmtSpeed(t.upspeed) : '—' }}</td>
              <td class="num">{{ t.status === 'downloading' ? fmtEta(t.eta) : '—' }}</td>
              <td class="num">{{ t.seeds ?? 0 }} / {{ t.peers ?? 0 }}</td>
              <td class="ops">
                <button v-if="t.status === 'downloading' && !t.paused" class="icon-btn"
                        title="暂停下载" aria-label="暂停下载" @click="act(t, 'pause')">
                  <Icon name="pause" :size="14" />
                </button>
                <button v-else-if="t.status === 'downloading' && t.paused" class="icon-btn"
                        title="恢复下载" aria-label="恢复下载" @click="act(t, 'resume')">
                  <Icon name="play" :size="14" />
                </button>
                <button v-else-if="t.status === 'completed'" class="icon-btn"
                        title="重新执行媒体探测与入库" aria-label="重试入库" @click="act(t, 'postprocess')">
                  <Icon name="refresh" :size="14" />
                </button>
                <button class="icon-btn" title="删除任务" aria-label="删除任务" @click="askDelete([t.id])">
                  <Icon name="trash" :size="14" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <h3 v-if="visibleHistory.length || (!attentionOnly && store.lastSync && !store.syncError)" class="section">{{ attentionOnly ? '失败任务' : '历史' }}</h3>
    <div v-if="!attentionOnly && store.lastSync && !store.syncError && !store.history.length" class="muted">暂无历史记录</div>
    <div v-for="t in visibleHistory" :key="t.id" class="card hist" :class="{ sel: isSel(t.id) }">
      <input type="checkbox" class="ck" :checked="isSel(t.id)" @change="toggle(t.id)" />
      <span class="tag" :class="statusText(t)[1]">{{ statusText(t)[0] }}</span>
      <div class="hist-title muted" :title="t.title_raw">{{ t.title_raw }}</div>
      <div class="spacer" />
      <span class="muted" style="font-size: 12px;" v-if="t.error_message">{{ t.error_message }}</span>
      <button v-if="['download_error', 'submit_failed'].includes(t.status)" class="btn sm" @click="act(t, 'resume')"><Icon name="refresh" :size="13" /> 重试</button>
      <button class="btn sm danger" @click="askDelete([t.id])"><Icon name="trash" :size="13" /> 删除</button>
    </div>

    <div v-if="delConfirm" class="modal-mask" @click.self="closeDelete">
      <div class="modal" style="width: 420px;">
        <h3 style="margin-bottom: 10px;">删除 {{ delConfirm.ids.length }} 个任务</h3>
        <p class="muted" style="font-size: 12.5px;">从下载器移除并从列表清除,之后不会自动重新下载。</p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="delConfirm.deleteFiles" :disabled="busy" /> 同时删除已下载的文件(不可恢复)
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" :disabled="busy" @click="closeDelete">取消</button>
          <button class="btn danger" :disabled="busy" @click="doDelete">确认删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page > .row { flex-wrap: wrap; }
.sync-status { color: var(--text-dim); font-size: 13px; }
.sync-status:not(:empty) { margin: 12px 0; }
.task-error { color: var(--red); font-size: 12px; white-space: normal; }
.batch-bar { display: flex; align-items: center; gap: 8px; padding: 10px 16px; margin-bottom: 12px;
  position: sticky; top: 8px; z-index: 10; border-color: var(--accent-dim); }
.grp { margin-bottom: 18px; }
.grp-title { font-size: 14px; color: var(--accent); border-left: 3px solid var(--accent);
  padding-left: 10px; margin-bottom: 8px; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th { text-align: left; color: var(--text-dim); font-weight: 600; padding: 8px 10px;
  border-bottom: 1px solid var(--border); white-space: nowrap; }
.tbl td { padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; vertical-align: middle; }
.tbl tr:last-child td { border-bottom: none; }
.tbl tr.sel { background: rgba(246,160,77,.08); }
.ck-col { width: 28px; }
.ck { accent-color: var(--accent); cursor: pointer; }
.name-col { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.prog { display: flex; align-items: center; gap: 6px; min-width: 110px; }
.bar { flex: 1; height: 6px; background: #252d3d; border-radius: 4px; overflow: hidden; min-width: 60px; }
.fill { height: 100%; background: var(--accent); }
.num { text-align: right; color: var(--text-dim); }
.ops { text-align: center; }
.icon-btn { border: none; background: transparent; cursor: pointer; color: var(--text-dim); font-size: 13px; }
.icon-btn:hover { color: var(--text); }
.op-msg { color: var(--red); font-size: 12.5px; margin: 0 0 10px; }
.section { font-size: 14px; color: var(--text-dim); margin: 18px 0 10px; font-weight: 600; }
.hist { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; padding: 10px 14px; }
.hist.sel { border-color: var(--accent); }
.hist-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 55%; font-size: 13px; }

@media (max-width: 768px) {
  /* 历史行:标题占首行铺满,重试/删除换到下一行,避免被挤出屏幕 */
  .hist { flex-wrap: wrap; row-gap: 6px; }
  .hist-title { max-width: 100%; flex: 1 1 140px; }
  .hist > .spacer { display: none; }
}
</style>
