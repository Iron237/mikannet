// SQLite 快照决定哪些任务可见，WebSocket 只补同一状态下的实时指标。
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api'

const activeStates = new Set(['downloading', 'pending', 'completed'])
const errorStates = new Set(['download_error', 'submit_failed'])
const liveFields = ['progress', 'dlspeed', 'size', 'eta', 'state', 'paused', 'upspeed', 'seeds', 'peers']

function liveValues(row) {
  return Object.fromEntries(liveFields.map(key => [key, row[key]]))
}

export const useTasksStore = defineStore('tasks', () => {
  const tasks = ref([])
  const byId = computed(() => new Map(tasks.value.map(t => [t.id, t])))
  const active = computed(() => tasks.value.filter(t => activeStates.has(t.status)))
  const history = computed(() => tasks.value.filter(t => errorStates.has(t.status) || t.status === 'archived'))
  const needsAttention = computed(() => tasks.value.filter(t =>
    errorStates.has(t.status) || (t.status === 'completed' && t.error_message)))
  const wsConnected = ref(false)
  const liveAvailable = ref(null)
  const lastSync = ref(null)
  const syncError = ref('')
  const loading = ref(false)
  let cursor = null
  let watching = []
  let flight = null
  let received = null
  let socket = null
  let retry = 0
  let reconnectTimer
  let syncTimer

  async function sync(full) {
    loading.value = true
    received = new Map()
    try {
      const snapshot = await api.post('/api/tasks/sync', {
        after_id: full ? null : cursor,
        watching: full ? [] : watching,
      })
      const additions = []
      const removed = new Set(snapshot.removed)
      for (const row of snapshot.tasks) {
        const existing = snapshot.full ? null : byId.value.get(row.id)
        // 增量快照不访问下载器，保留仅 WS 提供的暂停状态与指标。
        if (existing?.status === row.status) {
          for (const key of ['state', 'paused', 'upspeed', 'seeds', 'peers', 'eta']) row[key] = existing[key]
        }
        const update = received.get(row.id)
        if (update?.status === row.status) Object.assign(row, liveValues(update))
        if (existing) Object.assign(existing, row)
        else additions.push(row)
      }
      if (snapshot.full) tasks.value = additions  // 后端已按创建时间排序
      else if (additions.length || removed.size) {
        tasks.value = [...additions, ...tasks.value.filter(t => !removed.has(t.id))]
          .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id)
      }
      cursor = snapshot.cursor
      watching = snapshot.tasks.filter(t => t.status !== 'archived').map(t => t.id)
      if (snapshot.full || snapshot.live_available !== null) liveAvailable.value = snapshot.live_available
      syncError.value = ''
      lastSync.value = Date.now()
    } catch (e) {
      syncError.value = e.message || '服务器未响应'
    } finally {
      received = null
      loading.value = false
    }
  }

  function load(full = true) {
    if (flight) return flight
    flight = sync(full || cursor === null).finally(() => { flight = null })
    return flight
  }

  // 手动操作、回到前台或重连后，需要在旧请求完成后再取一次完整快照。
  async function refresh() {
    if (flight) await flight
    return load()
  }

  function connect() {
    if (socket) return
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/progress`)
    socket = ws
    ws.onopen = () => {
      wsConnected.value = true
      if (retry) refresh()
      retry = 0
    }
    ws.onmessage = event => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'tracker-error') { liveAvailable.value = false; return }
      if (msg.type !== 'tasks') return
      liveAvailable.value = true
      for (const update of msg.tasks) {
        const row = byId.value.get(update.id)
        if (row?.status === update.status) Object.assign(row, liveValues(update))
        received?.set(update.id, update)
      }
    }
    ws.onclose = () => {
      wsConnected.value = false
      socket = null
      reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 15000))
    }
    ws.onerror = () => ws.close()
  }

  function onVisible() { if (!document.hidden) refresh() }
  function start() {
    if (syncTimer) return
    load()
    connect()
    document.addEventListener('visibilitychange', onVisible)
    syncTimer = setInterval(() => { if (!document.hidden) load(false) }, 15000)
  }
  function stop() {
    clearInterval(syncTimer)
    clearTimeout(reconnectTimer)
    syncTimer = null
    document.removeEventListener('visibilitychange', onVisible)
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
    wsConnected.value = false
  }
  return { tasks, active, history, needsAttention, wsConnected, liveAvailable, lastSync,
    syncError, loading, load, refresh, start, stop }
})
