<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const data = ref(null)
const DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const today = (new Date().getDay() + 6) % 7   // JS 周日=0 → 我们 周一=0

// 从今天开始排列一周
const ordered = computed(() => {
  if (!data.value) return []
  return Array.from({ length: 7 }, (_, i) => {
    const d = (today + i) % 7
    return { day: d, name: DAY_NAMES[d], list: data.value.days[d], isToday: i === 0 }
  })
})

onMounted(async () => {
  data.value = await api.get('/api/bangumi/calendar/week')
})
</script>

<template>
  <div class="page">
    <div class="page-title">放送日历 <span class="muted" style="font-size: 13px;">(连载中)</span></div>
    <div v-if="!data" class="muted">加载中…</div>
    <template v-else>
      <div class="week">
        <section v-for="d in ordered" :key="d.day" class="day" :class="{ today: d.isToday }">
          <h3>{{ d.name }} <span v-if="d.isToday" class="tag accent">今天</span></h3>
          <div v-if="!d.list.length" class="muted" style="font-size: 12px;">—</div>
          <RouterLink v-for="b in d.list" :key="b.id" :to="`/bangumi/${b.id}`" class="cal-item">
            <img v-if="b.poster" :src="b.poster" loading="lazy" />
            <div class="cal-info">
              <div class="cal-title">{{ b.title }}</div>
              <div class="muted" style="font-size: 11px;">
                {{ b.eps_downloaded }}{{ b.eps_total ? '/' + b.eps_total : '' }} 集
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
@media (max-width: 768px) {
  .week { grid-template-columns: 1fr 1fr; }
}
</style>
