<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useTasksStore } from './stores/tasks'
import Icon from './components/Icon.vue'
import NativeLaunchModal from './components/NativeLaunchModal.vue'

const route = useRoute()
const bare = computed(() => route.name === 'setup')   // 首次配置向导:全屏,无侧边栏
const drawerOpen = ref(false)                          // 手机端左侧抽屉
watch(() => route.fullPath, () => { drawerOpen.value = false })   // 切页自动收起抽屉
const tasksStore = useTasksStore()
onMounted(() => {
  tasksStore.load()
  tasksStore.connect()
})
</script>

<template>
  <RouterView v-if="bare" />
  <div v-else class="layout">
    <!-- 手机顶栏:汉堡按钮唤出左侧抽屉(桌面端隐藏)-->
    <header class="topbar">
      <button class="hamburger" aria-label="菜单" @click="drawerOpen = true"><Icon name="menu" :size="22" /></button>
      <span class="topbar-logo">🍊 Mikanarr</span>
    </header>
    <div class="drawer-backdrop" :class="{ show: drawerOpen }" @click="drawerOpen = false" />

    <aside class="sidebar" :class="{ open: drawerOpen }">
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

/* 顶栏 + 抽屉遮罩:仅手机端显示 */
.topbar { display: none; }
.drawer-backdrop { display: none; }

/* 手机端:侧边栏改为左侧滑出抽屉(点汉堡唤出),不再占用底部 → 页面无需左右滑动 */
@media (max-width: 768px) {
  .topbar {
    display: flex; align-items: center; gap: 12px;
    position: fixed; top: 0; left: 0; right: 0; height: 48px; z-index: 40;
    padding: 0 14px; background: var(--bg-card); border-bottom: 1px solid var(--border);
  }
  .hamburger {
    background: none; border: none; color: var(--text); cursor: pointer;
    display: flex; align-items: center; padding: 4px; margin-left: -4px;
  }
  .topbar-logo { font-size: 16px; font-weight: 800; letter-spacing: .5px; }

  .drawer-backdrop {
    display: block; position: fixed; inset: 0; z-index: 45;
    background: rgba(0,0,0,.5); opacity: 0; pointer-events: none; transition: opacity .2s;
  }
  .drawer-backdrop.show { opacity: 1; pointer-events: auto; }

  .sidebar {
    position: fixed; top: 0; left: 0; bottom: 0; height: 100vh; width: 234px; z-index: 46;
    transform: translateX(-100%); transition: transform .22s ease;
    background: var(--bg-card); border-right: 1px solid var(--border);
    box-shadow: 4px 0 20px rgba(0,0,0,.35);
  }
  .sidebar.open { transform: translateX(0); }
  .logo, .ws-status { display: flex; }
  .nav-item { font-size: 14px; }

  .content { padding-top: 48px; min-width: 0; }
}
</style>
