<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import Icon from '../components/Icon.vue'

const data = ref(null)
const DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const today = (new Date().getDay() + 6) % 7   // JS 周日=0 → 我们 周一=0

// 从今天开始排列一周;每列带具体日期(年月日)
const ordered = computed(() => {
  if (!data.value) return []
  return Array.from({ length: 7 }, (_, i) => {
    const d = (today + i) % 7
    const date = new Date()
    date.setDate(date.getDate() + i)
    return {
      day: d, name: DAY_NAMES[d], list: data.value.days[d], isToday: i === 0,
      dateLabel: `${date.getMonth() + 1}月${date.getDate()}日`,
      yearLabel: `${date.getFullYear()}`,
    }
  })
})

// 官方还没开播的番:显示「N月N日开播」而不是集数
function airLabel(b) {
  if (!b.air_date) return null
  const d = new Date(b.air_date.slice(0, 10).replaceAll('/', '-'))
  if (isNaN(d) || d <= new Date()) return null
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日开播`
}

async function load() {
  try { data.value = await api.get('/api/bangumi/calendar/week') } catch { /* 忽略,保留上次 */ }
}

// 手动刷新:重拉 bgm.tv 放送信息(延期/提档检测),结果就地展示
const refreshing = ref(false)
const refreshMsg = ref('')
async function refreshAir() {
  refreshing.value = true
  refreshMsg.value = ''
  try {
    const r = await api.post('/api/bangumi/calendar/refresh')
    refreshMsg.value = r.changed.length
      ? `已检查 ${r.checked} 部,日期变动:` + r.changed.map(c =>
          `${c.title}${c.number != null ? ` 第${c.number}话` : ''} ${c.old}→${c.new}`).join(';')
      : `已检查 ${r.checked} 部,放送日期无变动` + (r.failed ? `(${r.failed} 部获取失败)` : '')
    await load()
  } catch (e) { refreshMsg.value = e.message }
  finally { refreshing.value = false }
}

// 标签页重新可见时刷新:番剧库新发现的集(RSS/智能下载扫到)即时反映,免手动重载
function onVisible() { if (document.visibilityState === 'visible') load() }
onMounted(() => { load(); document.addEventListener('visibilitychange', onVisible) })
onUnmounted(() => document.removeEventListener('visibilitychange', onVisible))
</script>

<template>
  <div class="page">
    <div class="row" style="align-items: center; gap: 10px; flex-wrap: wrap;">
      <div class="page-title" style="margin: 0;">放送日历 <span class="muted" style="font-size: 13px;">(连载中)</span></div>
      <div class="spacer" />
      <button class="btn sm" :disabled="refreshing" title="重新从 bgm.tv 获取放送日期(检测延期/提档);系统也会每 12 小时自动检测并推送变动"
              @click="refreshAir">
        <Icon name="refresh" :size="13" /> {{ refreshing ? '获取中…' : '刷新放送信息' }}
      </button>
    </div>
    <p v-if="refreshMsg" class="muted" style="font-size: 12px; margin: 6px 0 10px;">{{ refreshMsg }}</p>
    <div v-if="!data" class="muted">加载中…</div>
    <template v-else>
      <div class="week">
        <section v-for="d in ordered" :key="d.day" class="day" :class="{ today: d.isToday }">
          <h3>
            {{ d.name }}
            <span class="muted cal-date" :title="d.yearLabel + '年'">{{ d.dateLabel }}</span>
            <span v-if="d.isToday" class="tag accent">今天</span>
          </h3>
          <div v-if="!d.list.length" class="muted" style="font-size: 12px;">—</div>
          <RouterLink v-for="b in d.list" :key="b.id" :to="`/bangumi/${b.id}`" class="cal-item">
            <img v-if="b.poster" :src="b.poster" loading="lazy" />
            <div class="cal-info">
              <div class="cal-title">{{ b.title }}</div>
              <div class="muted cal-eps">
                <!-- 前瞻视角:放送表展示「本周将更新什么」,而不是已经有什么 -->
                <template v-if="b.upcoming && !b.upcoming.over">
                  <span class="upcoming" :title="`${b.upcoming.date} 更新`">
                    {{ b.upcoming.premiere ? '开播·' : '' }}第 {{ b.upcoming.number }} 话</span>
                  · 已下载 {{ b.eps_downloaded }}
                  <span v-if="b.eps_aired != null && b.eps_aired > b.eps_downloaded"
                        class="new-ep" title="有已发布未下载的集">● 新集</span>
                </template>
                <template v-else-if="b.upcoming && b.upcoming.over">
                  <span title="按周更推算本季集数已播完,等待完结确认">本季已播完</span>
                  · 已下载 {{ b.eps_downloaded }}{{ b.eps_total ? '/' + b.eps_total : '' }}
                </template>
                <template v-else-if="airLabel(b)">
                  <!-- 官方未开播且本周不首播:显示开播日期 -->
                  <span class="upcoming">{{ airLabel(b) }}</span>
                </template>
                <template v-else>
                  已下载 {{ b.eps_downloaded }}{{ b.eps_total ? '/' + b.eps_total : '' }} 集
                </template>
                <span v-if="b.score" style="color: var(--accent);">★{{ b.score }}</span>
              </div>
            </div>
          </RouterLink>
        </section>
      </div>
      <div v-if="data.unknown.length" style="margin-top: 18px;">
        <h3 class="muted" style="font-size: 13px; margin-bottom: 8px;">未知放送日</h3>
        <div class="row" style="flex-wrap: wrap;">
          <RouterLink v-for="b in data.unknown" :key="b.id" :to="`/bangumi/${b.id}`" class="tag">
            {{ b.title }}
          </RouterLink>
        </div>
      </div>
    </template>
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
.cal-date { font-size: 11px; font-weight: 400; }
.upcoming { color: var(--accent); font-weight: 600; }
.cal-item {
  display: flex; gap: 8px; align-items: center;
  padding: 6px; border-radius: 8px; margin-bottom: 4px; transition: background .15s;
}
.cal-item:hover { background: var(--bg-hover); }
.cal-item img { width: 36px; aspect-ratio: 5/7; object-fit: cover; border-radius: 5px; }
.cal-title {
  font-size: 12.5px; font-weight: 600;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.cal-eps { font-size: 11px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.new-ep { color: var(--red, #e5484d); font-weight: 700; font-size: 10.5px; }
@media (max-width: 768px) {
  .week { grid-template-columns: 1fr 1fr; }
}
</style>
