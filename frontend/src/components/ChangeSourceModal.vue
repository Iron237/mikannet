<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from './Icon.vue'
import RulePreview from './RulePreview.vue'

const props = defineProps({
  sub: { type: Object, required: true },
  bangumi: { type: Object, required: true },
})
const emit = defineEmits(['close', 'done'])

const detail = ref(null)
const selected = ref(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const preview = ref({ pass: -1, total: 0, error: '' })
const groups = computed(() =>
  (detail.value?.subgroups || []).filter(g => String(g.subgroup_id) !== String(props.sub.mikan_subgroup_id)))

async function load() {
  try {
    detail.value = await api.get(`/api/search/bangumi/${props.bangumi.mikan_bangumi_id}`)
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function replace() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    await api.post(`/api/subscriptions/${props.sub.id}/replace-source`, {
      mikan_subgroup_id: selected.value.subgroup_id,
      subgroup_name: selected.value.name,
    })
    emit('done')
  } catch (e) { error.value = e.message; saving.value = false }
}

onMounted(load)
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal source-modal">
      <div class="row head">
        <Icon name="refresh" :size="16" />
        <h3>更换订阅源</h3>
        <div class="spacer" />
        <button class="btn sm" aria-label="关闭更换订阅源" @click="emit('close')">
          <Icon name="close" :size="13" />
        </button>
      </div>
      <p class="muted intro">
        当前来源:<b>{{ sub.subgroup_name || sub.mikan_subgroup_id }}</b>。更换后保留过滤规则、
        下载路径和历史任务;旧来源的逐条手动勾选会清空。
      </p>
      <p v-if="error" class="err"><Icon name="alert" :size="13" /> {{ error }}</p>
      <div v-if="loading" class="muted body">正在加载可用字幕组…</div>
      <template v-else>
        <div class="group-grid">
          <button v-for="g in groups" :key="g.subgroup_id" class="group"
                  :class="{ on: selected?.subgroup_id === g.subgroup_id }"
                  @click="selected = g; preview = { pass: -1, total: 0, error: '' }">
            <span class="row"><strong>{{ g.name }}</strong><span class="spacer" />
              <span class="tag">{{ g.torrent_count }} 个源</span></span>
            <span class="caps">
              <span v-for="r in g.caps?.resolutions || []" :key="'r'+r" class="tag blue">{{ r }}</span>
              <span v-for="l in g.caps?.subtitle_langs || []" :key="'l'+l" class="tag green">{{ l }}</span>
              <span v-for="s in g.caps?.sources || []" :key="'s'+s" class="tag"
                    :class="s === 'BD' ? 'accent' : ''">{{ s }}</span>
              <span v-if="g.caps?.has_batch" class="tag">含合集</span>
            </span>
          </button>
        </div>
        <RulePreview v-if="selected"
                     :bangumi-id="bangumi.mikan_bangumi_id"
                     :subgroup-id="selected.subgroup_id"
                     :include="(sub.include_keywords || []).join(' ')"
                     :exclude="(sub.exclude_keywords || []).join(' ')"
                     :exclude-batch="sub.exclude_batch"
                     :eps-total="bangumi.eps_total || 0"
                     @stats="preview = $event" />
        <div class="row foot">
          <span class="muted">切换会立即检查新来源,不会删除旧任务或文件。</span>
          <div class="spacer" />
          <button class="btn" @click="emit('close')">取消</button>
          <button class="btn primary"
                  :disabled="saving || !selected || preview.pass <= 0 || !!preview.error"
                  @click="replace">
            {{ saving ? '切换中…' : '确认更换' }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.source-modal { width: min(880px, 95vw); max-height: 92vh; overflow-y: auto; }
.head { margin-bottom: 8px; }
.intro { font-size: 12.5px; line-height: 1.65; margin-bottom: 12px; }
.intro b { color: var(--text); margin: 0 4px; }
.err { color: var(--red); font-size: 12.5px; display: flex; gap: 6px; align-items: center; }
.body { padding: 24px 0; }
.group-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px; max-height: 260px; overflow-y: auto; margin-bottom: 8px; }
.group { text-align: left; padding: 10px 12px; color: var(--text); background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 8px; cursor: pointer; }
.group:hover, .group.on { border-color: var(--accent); background: var(--bg-hover); }
.caps { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 7px; }
.foot { margin-top: 14px; gap: 8px; font-size: 12px; }
@media (max-width: 700px) { .group-grid { grid-template-columns: 1fr; } }
</style>
