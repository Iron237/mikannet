// 下载任务 store:REST 拉全量,WebSocket 增量刷新,自动重连。
import { defineStore } from 'pinia'
import { api } from '../api'

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    tasks: [],
    wsConnected: false,
    _ws: null,
    _retry: 0,
  }),
  getters: {
    active: (s) => s.tasks.filter(t => ['downloading', 'pending', 'completed'].includes(t.status)),
    // 历史只显示已入库/出错;skipped(去重淘汰、坏种、已删除留痕)不在下载页展示
    history: (s) => s.tasks.filter(t => ['archived', 'download_error', 'submit_failed'].includes(t.status)),
  },
  actions: {
    async load() {
      this.tasks = await api.get('/api/tasks')
    },
    connect() {
      if (this._ws) return
      const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/progress`
      const ws = new WebSocket(url)
      this._ws = ws
      ws.onopen = () => { this.wsConnected = true; this._retry = 0 }
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        if (msg.type !== 'tasks') return
        for (const u of msg.tasks) {
          const t = this.tasks.find(x => x.id === u.id)
          if (t) Object.assign(t, {
            status: u.status, progress: u.progress, dlspeed: u.dlspeed,
            size: u.size, eta: u.eta, state: u.state,
            upspeed: u.upspeed, seeds: u.seeds, peers: u.peers,
          })
          else this.load()   // 出现未知任务,全量刷新
        }
      }
      ws.onclose = () => {
        this.wsConnected = false
        this._ws = null
        const delay = Math.min(1000 * 2 ** this._retry++, 15000)
        setTimeout(() => this.connect(), delay)
      }
      ws.onerror = () => ws.close()
    },
  },
})
