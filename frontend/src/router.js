import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'library', component: () => import('./views/LibraryView.vue') },
    { path: '/search', name: 'search', component: () => import('./views/SearchView.vue') },
    { path: '/bangumi/:id', name: 'bangumi', component: () => import('./views/BangumiDetailView.vue') },
    { path: '/calendar', name: 'calendar', component: () => import('./views/CalendarView.vue') },
    { path: '/bd', name: 'bd', component: () => import('./views/BdLibraryView.vue') },
    { path: '/subscriptions', name: 'subscriptions', component: () => import('./views/SubscriptionsView.vue') },
    { path: '/downloads', name: 'downloads', component: () => import('./views/DownloadsView.vue') },
    { path: '/logs', name: 'logs', component: () => import('./views/LogView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  ],
})
