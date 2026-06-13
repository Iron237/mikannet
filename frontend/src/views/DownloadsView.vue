<script setup>
import { computed, ref } from 'vue'
import { api, fmtSize, fmtSpeed } from '../api'
import { useTasksStore } from '../stores/tasks'

const store = useTasksStore()
const confirmDelete = ref(null)   // 单条待确认删除
const deleteFiles = ref(false)
const selected = ref(new Set())   // 多选任务 id
const confirmBatch = ref(false)   // 批量删除确认
const busy = ref(false)

const statusLabel = {
  pending: ['等待提交', 'blue'], downloading: ['下载中', 'accent'],
  completed: ['已完成', 'green'], archived: ['已入库', 'green'],
  submit_failed: ['提交失败', 'red'], download_error: ['下载错误', 'red'],
  skipped: ['已跳过', ''],
}

const allIds = computed(() => [...store.active, ...store.history].map(t => t.id))
const allSelected = computed(() => allIds.value.length > 0 && selected.value.size === allIds.value.length)

function isSel(id) { return selected.value.has(id) }
function toggle(id) {
  const s = new Set(selected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selected.value = s
}
function selectAll() { selected.value = new Set(allIds.value) }
function clearSel() { selected.value = new Set() }

async function act(task, action) {
  await api.post(`/api/tasks/${task.id}/${action}`)
  await store.load()
}

async function doDelete() {
  await api.delete(`/api/tasks/${confirmDelete.value.id}?delete_files=${deleteFiles.value}`)
  confirmDelete.value = null
  deleteFiles.value = false
  await store.load()
}

async function batchAct(action) {
  if (action === 'delete') { confirmBatch.value = true; return }
  busy.value = true
  try {
    await api.post('/api/tasks/batch', { ids: [...selected.value], action })
    clearSel(); await store.load()
  } finally { busy.value = false }
}

async function doBatchDelete() {
  busy.value = true
  try {
    await api.post('/api/tasks/batch',
      { ids: [...selected.value], action: 'delete', delete_files: deleteFiles.value })
    confirmBatch.value = false; deleteFiles.value = false; clearSel(); await store.load()
  } finally { busy.value = false }
}
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 8px;">
      <div class="page-title" style="margin: 0;">下载任务</div>
      <div class="spacer" />
      <button class="btn sm" @click="allSelected ? clearSel() : selectAll()">
        {{ allSelected ? '取消全选' : '全选' }}
      </button>
      <button class="btn sm" :disabled="!selected.size" @click="clearSel">清空选择</button>
    </div>

    <!-- 批量操作条 -->
    <div v-if="selected.size" class="batch-bar card">
      <strong>已选 {{ selected.size }} 项</strong>
      <div class="spacer" />
      <button class="btn sm" :disabled="busy" @click="batchAct('pause')">⏸ 批量暂停</button>
      <button class="btn sm" :disabled="busy" @click="batchAct('resume')">▶ 批量恢复</button>
      <button class="btn sm danger" :disabled="busy" @click="batchAct('delete')">🗑 批量删除</button>
    </div>

    <h3 class="section">进行中</h3>
    <div v-if="!store.active.length" class="muted" style="margin-bottom: 24px;">没有进行中的任务</div>
    <div v-for="t in store.active" :key="t.id" class="card task" :class="{ sel: isSel(t.id) }">
      <div class="row">
        <input type="checkbox" class="ck" :checked="isSel(t.id)" @change="toggle(t.id)" />
        <span class="tag" :class="statusLabel[t.status]?.[1]">{{ statusLabel[t.status]?.[0] ?? t.status }}</span>
        <div class="task-title" :title="t.title_raw">{{ t.title_raw }}</div>
        <div class="spacer" />
        <template v-if="t.status === 'downloading'">
          <button class="btn sm" @click="act(t, 'pause')">⏸ 暂停</button>
          <button class="btn sm" @click="act(t, 'resume')">▶ 恢复</button>
        </template>
        <button class="btn sm danger" @click="confirmDelete = t">🗑 删除</button>
      </div>
      <div class="row" style="margin-top: 10px;">
        <div class="progress-track" style="flex: 1;">
          <div class="progress-bar" :style="{ width: (t.progress * 100).toFixed(1) + '%' }" />
        </div>
        <span class="pct">{{ (t.progress * 100).toFixed(1) }}%</span>
      </div>
      <div class="row muted" style="margin-top: 6px; font-size: 12px;">
        <span>{{ fmtSize(t.size) }}</span>
        <span v-if="t.status === 'downloading'">{{ fmtSpeed(t.dlspeed) }}</span>
        <span v-if="t.episodes?.length">第 {{ t.episodes.join(', ') }} 话</span>
        <span v-if="t.is_batch" class="tag">合集</span>
        <span v-if="t.version > 1" class="tag accent">v{{ t.version }}</span>
      </div>
    </div>

    <h3 class="section">历史</h3>
    <div v-if="!store.history.length" class="muted">暂无历史记录</div>
    <div v-for="t in store.history" :key="t.id" class="card task slim" :class="{ sel: isSel(t.id) }">
      <div class="row">
        <input type="checkbox" class="ck" :checked="isSel(t.id)" @change="toggle(t.id)" />
        <span class="tag" :class="statusLabel[t.status]?.[1]">{{ statusLabel[t.status]?.[0] ?? t.status }}</span>
        <div class="task-title muted" :title="t.title_raw">{{ t.title_raw }}</div>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;" v-if="t.error_message">{{ t.error_message }}</span>
        <button v-if="['download_error', 'submit_failed'].includes(t.status)"
                class="btn sm" @click="act(t, 'resume')">↻ 重试</button>
      </div>
    </div>

    <div v-if="confirmDelete" class="modal-mask" @click.self="confirmDelete = null">
      <div class="modal" style="width: 420px;">
        <h3 style="margin-bottom: 10px;">删除任务</h3>
        <p class="muted" style="word-break: break-all;">{{ confirmDelete.title_raw }}</p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="deleteFiles" />
          同时删除已下载的文件
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="confirmDelete = null">取消</button>
          <button class="btn danger" @click="doDelete">确认删除</button>
        </div>
      </div>
    </div>

    <div v-if="confirmBatch" class="modal-mask" @click.self="confirmBatch = false">
      <div class="modal" style="width: 420px;">
        <h3 style="margin-bottom: 10px;">批量删除 {{ selected.size }} 个任务</h3>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="deleteFiles" />
          同时删除已下载的文件
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="confirmBatch = false">取消</button>
          <button class="btn danger" :disabled="busy" @click="doBatchDelete">确认删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section { font-size: 14px; color: var(--text-dim); margin: 18px 0 10px; font-weight: 600; }
.task { margin-bottom: 10px; padding: 14px 16px; }
.task.slim { padding: 10px 16px; }
.task.sel { border-color: var(--accent); }
.ck { accent-color: var(--accent); flex-shrink: 0; cursor: pointer; }
.task-title {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-width: 60%; font-size: 13px;
}
.pct { font-size: 12px; color: var(--accent); width: 52px; text-align: right; }
.batch-bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 16px; margin-bottom: 12px;
  position: sticky; top: 8px; z-index: 10; border-color: var(--accent-dim);
}
</style>
