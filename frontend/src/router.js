import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/setup', name: 'setup', component: () => import('./views/SetupView.vue') },
    { path: '/', name: 'library', component: () => import('./views/LibraryView.vue') },
    { path: '/search', name: 'search', component: () => import('./views/SearchView.vue') },
    { path: '/bangumi/:id', name: 'bangumi', component: () => import('./views/BangumiDetailView.vue') },
    { path: '/calendar', name: 'calendar', component: () => import('./views/CalendarView.vue') },
    { path: '/discover', name: 'discover', component: () => import('./views/DiscoverView.vue') },
    { path: '/bd', name: 'bd', component: () => import('./views/BdLibraryView.vue') },
    { path: '/subscriptions', name: 'subscriptions', component: () => import('./views/SubscriptionsView.vue') },
    { path: '/downloads', name: 'downloads', component: () => import('./views/DownloadsView.vue') },
    { path: '/logs', name: 'logs', component: () => import('./views/LogView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  ],
  // 滚动记忆:浏览器后退/前进(如番剧库→详情→返回)恢复上次滚动位置;内容异步加载 → 轮询等
  //   页面高度到位再恢复(最多 ~1.5s);普通跳转回到顶部。覆盖番剧库/订阅/下载/BD/搜索等所有长页。
  scrollBehavior(to, from, savedPosition) {
    if (!savedPosition || !savedPosition.top) return savedPosition || { top: 0 }
    const target = savedPosition.top
    return new Promise(resolve => {
      let tries = 0
      const tick = () => {
        const max = document.documentElement.scrollHeight - window.innerHeight
        if (max >= target || tries >= 60) resolve(savedPosition)
        else { tries++; setTimeout(tick, 25) }
      }
      tick()
    })
  },
})

// 首次配置守卫:未配置(setup_done 假且无番剧数据)→ 强制进 /setup;已配置访问 /setup → 回主页。
let _configured = null   // null=未知,true/false=已知(避免每次导航都查)
router.beforeEach(async (to) => {
  if (_configured === null) {
    try {
      const r = await fetch('/api/setup/status')
      _configured = (await r.json()).configured
    } catch {
      _configured = true   // 接口异常不锁死用户
    }
  }
  if (!_configured && to.name !== 'setup') return { name: 'setup' }
  if (_configured && to.name === 'setup') return { name: 'library' }
  return true
})

export default router
