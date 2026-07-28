<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from './Icon.vue'

const data = ref(null)
const loading = ref(true)
const error = ref('')
const open = ref(true)

const total = computed(() => data.value?.summary?.total || 0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get('/api/bangumi/resource-issues')
    open.value = total.value > 0
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

defineExpose({ load })
onMounted(load)
</script>

<template>
  <section class="issue-center card" :class="{ clear: !total && !error }">
    <button class="issue-head" type="button" @click="open = !open">
      <Icon :name="total || error ? 'alert' : 'check'" :size="15" />
      <strong>待处理中心</strong>
      <span v-if="loading" class="muted">正在核对数据库与实际文件…</span>
      <template v-else-if="data">
        <span class="count" :class="{ bad: data.summary.error }">{{ total }}</span>
        <span class="muted">
          严重 {{ data.summary.error }} · 提醒 {{ data.summary.warning }} · 信息 {{ data.summary.info }}
        </span>
      </template>
      <span v-if="error" class="error">{{ error }}</span>
      <span class="spacer" />
      <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="14" />
    </button>

    <div v-if="open && !loading" class="issue-body">
      <div v-if="error" class="empty-row">
        检查失败
        <button class="btn xs" @click.stop="load"><Icon name="refresh" :size="12" /> 重试</button>
      </div>
      <div v-else-if="!total" class="empty-row">
        数据库记录、实际文件、订阅与下载任务均未发现待处理项。
      </div>
      <div v-for="group in data?.groups || []" :key="group.key"
           class="issue-group" :class="group.severity">
        <div class="group-title">
          <strong>{{ group.label }}</strong><span class="tag">{{ group.items.length }}</span>
        </div>
        <RouterLink v-for="(item, index) in group.items.slice(0, 8)"
                    :key="`${item.bangumi_id || 'bd'}-${index}`"
                    class="issue-row" :to="item.path">
          <span class="item-title">{{ item.title }}</span>
          <span class="muted">{{ item.detail }}</span>
          <Icon name="chevron-right" :size="13" />
        </RouterLink>
        <div v-if="group.items.length > 8" class="more muted">
          另有 {{ group.items.length - 8 }} 项，请按上方类型逐部处理
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.issue-center { margin-bottom: 14px; padding: 0; overflow: hidden; border-color: var(--accent-dim); }
.issue-center.clear { border-color: var(--border); }
.issue-head { width: 100%; border: 0; background: transparent; color: inherit; padding: 11px 14px;
  display: flex; align-items: center; gap: 8px; cursor: pointer; text-align: left; }
.issue-head .count { min-width: 23px; height: 20px; padding: 0 6px; border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center; background: var(--accent-dim);
  font-size: 12px; font-weight: 700; }
.issue-head .count.bad { background: color-mix(in srgb, var(--red) 24%, transparent); color: var(--red); }
.error { color: var(--red); font-size: 12px; }
.issue-body { border-top: 1px solid var(--border); padding: 8px 12px 11px; }
.issue-group { border-left: 3px solid var(--border); margin: 5px 0; padding: 3px 0 3px 9px; }
.issue-group.error { border-color: var(--red); }
.issue-group.warning { border-color: var(--accent); }
.issue-group.info { border-color: var(--text-dim); }
.group-title { display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 2px 0 3px; }
.issue-row { display: grid; grid-template-columns: minmax(120px, 0.7fr) minmax(180px, 1.3fr) auto;
  align-items: center; gap: 8px; padding: 5px 6px; border-radius: 6px; color: inherit;
  text-decoration: none; font-size: 12px; }
.issue-row:hover { background: var(--bg-hover); }
.item-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.more, .empty-row { padding: 7px 6px; font-size: 12px; }
.empty-row { display: flex; align-items: center; gap: 8px; }
.btn.xs { font-size: 11.5px; padding: 3px 8px; }
@media (max-width: 700px) {
  .issue-head .muted { display: none; }
  .issue-row { grid-template-columns: 1fr auto; }
  .issue-row .muted { grid-column: 1 / -1; grid-row: 2; }
}
</style>
