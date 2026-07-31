<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import Icon from './Icon.vue'

const CACHE_KEY = 'mikannet_message_center_v1'
function readCache() {
  try { return JSON.parse(localStorage.getItem(CACHE_KEY) || 'null') }
  catch { return null }
}

const route = useRoute()
const root = ref(null)
const data = ref(readCache())
const loading = ref(false)
const error = ref('')
const open = ref(false)
const stale = ref(!data.value)

const total = computed(() => data.value?.summary?.total || 0)
const level = computed(() => {
  if (data.value?.summary?.error) return 'error'
  if (data.value?.summary?.warning) return 'warning'
  if (data.value?.summary?.info) return 'info'
  return ''
})
const generatedText = computed(() => {
  const value = data.value?.generated_at
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
})

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get('/api/bangumi/resource-issues')
    localStorage.setItem(CACHE_KEY, JSON.stringify(data.value))
    stale.value = false
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) load()
}
function close() { open.value = false }
function onOutside(event) {
  if (open.value && root.value && !root.value.contains(event.target)) close()
}
function markStale() { stale.value = true }

watch(() => route.fullPath, close)
onMounted(() => {
  document.addEventListener('pointerdown', onOutside)
  window.addEventListener('mikannet:issues-stale', markStale)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onOutside)
  window.removeEventListener('mikannet:issues-stale', markStale)
})
</script>

<template>
  <div ref="root" class="message-center">
    <button class="message-trigger" type="button" :class="{ active: open, stale }"
            :aria-expanded="open" aria-label="消息中心"
            :title="total ? `消息中心：${total} 条待处理消息` : '消息中心'"
            @click="toggle">
      <Icon name="bell" :size="19" />
      <span v-if="total" class="message-dot" :class="level" />
    </button>

    <section v-if="open" class="message-panel" aria-live="polite">
      <header class="message-head">
        <div>
          <strong>消息中心</strong>
          <div class="message-summary muted" v-if="data">
            严重 {{ data.summary.error }} · 提醒 {{ data.summary.warning }} · 信息 {{ data.summary.info }}
          </div>
          <div class="message-summary muted" v-else>点击后按需核对，不影响页面加载</div>
        </div>
        <div class="spacer" />
        <button class="icon-btn" type="button" :disabled="loading"
                title="重新核对" aria-label="重新核对" @click="load">
          <Icon name="refresh" :size="15" :class="{ spinning: loading }" />
        </button>
        <button class="icon-btn" type="button" title="关闭" aria-label="关闭" @click="close">
          <Icon name="close" :size="16" />
        </button>
      </header>

      <div v-if="loading && !data" class="message-state">
        <Icon name="scan" :size="16" />
        正在核对数据库与实际文件…
      </div>
      <div v-if="error" class="message-state error">
        核对失败：{{ error }}
        <button class="btn xs" @click.stop="load">重试</button>
      </div>
      <div v-if="loading && data" class="refreshing muted">
        <Icon name="refresh" :size="12" class="spinning" /> 正在刷新
      </div>

      <div v-if="data" class="message-body">
        <div v-if="!total" class="message-state clear">
          <Icon name="check" :size="18" />
          暂无待处理消息
        </div>
        <div v-for="group in data.groups || []" :key="group.key"
             class="message-group" :class="group.severity">
          <div class="group-title">
            <span class="level-mark" />
            <strong>{{ group.label }}</strong>
            <span class="tag">{{ group.items.length }}</span>
          </div>
          <RouterLink v-for="(item, index) in group.items.slice(0, 50)"
                      :key="`${item.bangumi_id || 'bd'}-${index}`"
                      class="message-row" :to="item.path">
            <span class="item-title">{{ item.title }}</span>
            <span class="muted item-detail">{{ item.detail }}</span>
            <Icon name="chevron-right" :size="13" />
          </RouterLink>
          <p v-if="group.items.length > 50" class="message-more">
            另有 {{ group.items.length - 50 }} 条消息，请先处理当前列表后刷新。
          </p>
        </div>
      </div>
      <footer v-if="generatedText" class="message-foot muted">
        上次核对：{{ generatedText }}<span v-if="stale"> · 内容可能有更新</span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.message-center { position: fixed; top: 16px; right: 20px; z-index: 55; }
.message-trigger {
  position: relative; width: 40px; height: 40px; border-radius: 12px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--border); background: color-mix(in srgb, var(--bg-card) 92%, transparent);
  color: var(--text-dim); cursor: pointer; box-shadow: 0 8px 24px rgba(0,0,0,.18);
  backdrop-filter: blur(10px); transition: color .15s, border-color .15s, background .15s;
}
.message-trigger:hover, .message-trigger.active {
  color: var(--text); border-color: var(--accent-dim); background: var(--bg-card);
}
.message-trigger.stale::after {
  content: ''; position: absolute; inset: -3px; border: 1px solid color-mix(in srgb, var(--accent) 32%, transparent);
  border-radius: 14px; pointer-events: none;
}
.message-dot {
  position: absolute; top: 7px; right: 7px; width: 8px; height: 8px; border-radius: 50%;
  border: 2px solid var(--bg-card); box-sizing: content-box;
}
.message-dot.error { background: var(--red); }
.message-dot.warning { background: var(--accent); }
.message-dot.info { background: var(--blue, #4f8cff); }
.message-panel {
  position: absolute; top: 48px; right: 0; width: min(440px, calc(100vw - 24px));
  max-height: calc(100vh - 80px); display: flex; flex-direction: column;
  border: 1px solid var(--border); border-radius: 14px; background: var(--bg-card);
  box-shadow: 0 18px 60px rgba(0,0,0,.42); overflow: hidden;
}
.message-head { display: flex; align-items: center; gap: 7px; padding: 13px 14px;
  border-bottom: 1px solid var(--border); }
.message-summary { margin-top: 2px; font-size: 11.5px; }
.icon-btn {
  width: 30px; height: 30px; border: 0; border-radius: 8px; background: transparent;
  color: var(--text-dim); cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
}
.icon-btn:hover { background: var(--bg-hover); color: var(--text); }
.icon-btn:disabled { cursor: default; opacity: .55; }
.message-body { overflow-y: auto; padding: 8px 10px 10px; }
.message-group { margin: 5px 0 9px; }
.group-title { display: flex; align-items: center; gap: 7px; padding: 4px 6px;
  font-size: 12px; }
.level-mark { width: 7px; height: 7px; border-radius: 50%; background: var(--text-dim); }
.message-group.error .level-mark { background: var(--red); }
.message-group.warning .level-mark { background: var(--accent); }
.message-group.info .level-mark { background: var(--blue, #4f8cff); }
.message-row { display: grid; grid-template-columns: minmax(110px, .75fr) minmax(150px, 1.25fr) auto;
  align-items: center; gap: 8px; padding: 7px 8px; border-radius: 8px; color: inherit;
  text-decoration: none; font-size: 12px; }
.message-row:hover { background: var(--bg-hover); }
.item-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-detail { line-height: 1.45; }
.message-more { margin: 8px; color: var(--muted); font-size: 11.5px; }
.message-state { min-height: 120px; padding: 24px; display: flex; align-items: center;
  justify-content: center; gap: 8px; text-align: center; font-size: 12.5px; }
.message-state.error { color: var(--red); }
.message-state.clear { color: var(--green); }
.refreshing { padding: 6px 14px 0; font-size: 11.5px; display: flex; align-items: center; gap: 5px; }
.message-foot { border-top: 1px solid var(--border); padding: 8px 14px; font-size: 10.5px; }
.spinning { animation: spin .85s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.btn.xs { font-size: 11.5px; padding: 3px 8px; }
@media (max-width: 700px) {
  .message-center { top: 4px; right: 8px; z-index: 60; }
  .message-trigger { width: 40px; height: 40px; border: 0; box-shadow: none; background: transparent; }
  .message-panel { position: fixed; top: 52px; right: 8px; left: 8px;
    width: auto; max-height: calc(100vh - 64px); }
  .message-row { grid-template-columns: 1fr auto; }
  .message-row .item-detail { grid-column: 1 / -1; grid-row: 2; }
}
</style>
