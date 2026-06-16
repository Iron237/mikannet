<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useTasksStore } from './stores/tasks'
import Icon from './components/Icon.vue'
import NativeLaunchModal from './components/NativeLaunchModal.vue'

const route = useRoute()
const bare = computed(() => route.name === 'setup')   // 首次配置向导:全屏,无侧边栏
const tasksStore = useTasksStore()
onMounted(() => {
  tasksStore.load()
  tasksStore.connect()
})
</script>

<template>
  <RouterView v-if="bare" />
  <div v-else class="layout">
    <aside class="sidebar">
      <div class="logo">🍊 Mikanarr</div>
      <nav>
        <RouterLink to="/" class="nav-item"><Icon name="library" :size="17" /> 番剧库</RouterLink>
        <RouterLink to="/search" class="nav-item"><Icon name="search" :size="17" /> 搜索</RouterLink>
        <RouterLink to="/calendar" class="nav-item"><Icon name="calendar" :size="17" /> 放送表</RouterLink>
        <RouterLink to="/bd" class="nav-item"><Icon name="disc" :size="17" /> BD 收藏</RouterLink>
        <RouterLink to="/subscriptions" class="nav-item"><Icon name="rss" :size="17" /> 订阅管理</RouterLink>
        <RouterLink to="/downloads" class="nav-item">
          <Icon name="download" :size="17" /> 下载任务
          <span v-if="tasksStore.active.filter(t => t.status === 'downloading').length"
                class="badge">{{ tasksStore.active.filter(t => t.status === 'downloading').length }}</span>
        </RouterLink>
        <RouterLink to="/logs" class="nav-item"><Icon name="logs" :size="17" /> 日志</RouterLink>
        <RouterLink to="/settings" class="nav-item"><Icon name="settings" :size="17" /> 设置</RouterLink>
      </nav>
      <div class="spacer" />
      <div class="ws-status" :class="{ ok: tasksStore.wsConnected }">
        {{ tasksStore.wsConnected ? '● 实时连接' : '○ 连接断开' }}
      </div>
    </aside>
    <main class="content">
      <RouterView />
    </main>
    <NativeLaunchModal />
  </div>
</template>

<style scoped>
.layout { display: flex; min-height: 100vh; }
.sidebar {
  width: 210px; flex-shrink: 0; padding: 22px 14px;
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 4px;
  position: sticky; top: 0; height: 100vh;
}
.logo { font-size: 19px; font-weight: 800; padding: 0 10px 18px; letter-spacing: .5px; }
.sidebar nav { display: flex; flex-direction: column; }
.nav-item {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px; border-radius: 8px; color: var(--text-dim);
  transition: all .15s; margin-bottom: 2px;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text); }
.nav-item.router-link-active { background: var(--bg-hover); color: var(--accent); font-weight: 600; }
.badge {
  margin-left: auto; background: var(--accent); color: #1a1207;
  border-radius: 10px; padding: 0 7px; font-size: 11px; font-weight: 700;
}
.ws-status { font-size: 12px; color: var(--text-dim); padding: 0 10px; }
.ws-status.ok { color: var(--green); }
.content { flex: 1; min-width: 0; }

/* 手机端:侧边栏变底部导航 */
@media (max-width: 768px) {
  .layout { flex-direction: column; }
  .sidebar {
    width: 100%; height: auto; position: fixed; bottom: 0; top: auto; z-index: 50;
    flex-direction: row; align-items: center; padding: 6px 8px;
    border-right: none; border-top: 1px solid var(--border);
    background: var(--bg-card);
  }
  .logo, .ws-status, .sidebar .spacer { display: none; }
  .sidebar nav { flex-direction: row; flex: 1; justify-content: space-around; }
  .nav-item {
    flex-direction: column; gap: 2px; font-size: 11px;
    padding: 6px 10px; margin-bottom: 0;
  }
  .badge { margin-left: 0; position: absolute; transform: translate(14px, -4px); }
  .content { padding-bottom: 64px; }
}
</style>
