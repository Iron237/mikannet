<script setup>
// 发现:bgm.tv 每日放送(当季全站新番,不只已订阅的)+ 一键跳订阅 + 导入 bgm「在看」
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'

const data = ref(null)
const error = ref('')
const DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const today = (new Date().getDay() + 6) % 7

const ordered = computed(() => {
  if (!data.value) return []
  return Array.from({ length: 7 }, (_, i) => {
    const d = (today + i) % 7
    const date = new Date()
    date.setDate(date.getDate() + i)
    return {
      day: d, name: DAY_NAMES[d], list: data.value.days[d], isToday: i === 0,
      dateLabel: `${date.getMonth() + 1}月${date.getDate()}日`,
    }
  })
})

async function load() {
  error.value = ''
  try { data.value = await api.get('/api/discover/calendar') }
  catch (e) { error.value = e.message }
}

// 导入 bgm「在看」:需设置页配置个人令牌
const importing = ref(false)
const importMsg = ref('')
async function importWatching() {
  importing.value = true
  importMsg.value = ''
  try {
    const r = await api.post('/api/discover/import-watching')
    const names = r.imported.map(x => x.title + (x.mikan_matched ? '' : '(未匹配蜜柑)')).join('、')
    importMsg.value = `导入 ${r.imported.length} 部` + (names ? `:${names}` : '')
      + (r.existed ? `;已存在 ${r.existed} 部` : '') + (r.failed ? `;失败 ${r.failed} 部` : '')
    await load()
  } catch (e) { importMsg.value = e.message }
  finally { importing.value = false }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="row" style="align-items: center; gap: 10px; flex-wrap: wrap;">
      <div class="page-title" style="margin: 0;">
        发现 <span class="muted" style="font-size: 13px;">(bgm.tv 每日放送 · 当季全部新番)</span>
      </div>
      <div class="spacer" />
      <button class="btn sm" :disabled="importing"
              title="把 bgm.tv「在看」列表导入为库内番剧(需在设置页「bgm.tv 联动」配置个人令牌)"
              @click="importWatching">
        <Icon name="download" :size="13" /> {{ importing ? '导入中…' : '导入 bgm 在看' }}
      </button>
    </div>
    <p v-if="importMsg" class="muted" style="font-size: 12px; margin: 6px 0 10px;">{{ importMsg }}</p>
    <p v-if="error" class="op-msg">{{ error }}</p>
    <div v-if="!data && !error" class="muted">加载中…</div>
    <div v-else-if="data" class="week">
      <section v-for="d in ordered" :key="d.day" class="day" :class="{ today: d.isToday }">
        <h3>
          {{ d.name }} <span class="muted disc-date">{{ d.dateLabel }}</span>
          <span v-if="d.isToday" class="tag accent">今天</span>
        </h3>
        <div v-if="!d.list.length" class="muted" style="font-size: 12px;">—</div>
        <component v-for="b in d.list" :key="b.subject_id"
                   :is="b.local_id ? 'RouterLink' : 'RouterLink'"
                   :to="b.local_id ? `/bangumi/${b.local_id}` : `/search?searchstr=${encodeURIComponent(b.title)}`"
                   class="disc-item" :title="b.local_id ? '已入库,点击进入详情' : '点击去搜索订阅'">
          <img v-if="b.image" :src="b.image" loading="lazy" referrerpolicy="no-referrer" />
          <div class="disc-info">
            <div class="disc-title">{{ b.title }}</div>
            <div class="muted disc-meta">
              <span v-if="b.local_id" class="tag green" style="font-size: 10px;">已入库</span>
              <span v-else class="tag" style="font-size: 10px;">订阅</span>
              <span v-if="b.score" style="color: var(--accent);">★{{ b.score }}</span>
            </div>
          </div>
        </component>
      </section>
    </div>
  </div>
</template>

<style scoped>
.week {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}
.day {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px; min-height: 120px;
}
.day.today { border-color: var(--accent); }
.day h3 { font-size: 13.5px; margin-bottom: 10px; display: flex; gap: 6px; align-items: center; }
.disc-date { font-size: 11px; font-weight: 400; }
.disc-item {
  display: flex; gap: 8px; align-items: center;
  padding: 6px; border-radius: 8px; margin-bottom: 4px; transition: background .15s;
}
.disc-item:hover { background: var(--bg-hover); }
.disc-item img { width: 36px; aspect-ratio: 5/7; object-fit: cover; border-radius: 5px; }
.disc-title {
  font-size: 12.5px; font-weight: 600;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.disc-meta { font-size: 11px; display: flex; align-items: center; gap: 5px; }
@media (max-width: 768px) {
  .week { grid-template-columns: 1fr 1fr; }
}
</style>
