<script setup>
import { ref } from 'vue'
import { api } from '../api'
import RulePreview from './RulePreview.vue'
import Icon from './Icon.vue'

const props = defineProps({
  sub: { type: Object, required: true },   // SubscriptionOut
})
const emit = defineEmits(['close'])

const form = ref({
  include_keywords: (props.sub.include_keywords || []).join(' '),
  exclude_keywords: (props.sub.exclude_keywords || []).join(' '),
  exclude_batch: props.sub.exclude_batch,
  backfill: props.sub.backfill,
  episode_offset: props.sub.episode_offset || 0,
})
const overrides = ref(Object.fromEntries([
  ...(props.sub.pinned_guids || []).map(g => [g, true]),
  ...(props.sub.blocked_guids || []).map(g => [g, false]),
]))
const saving = ref(false)
const error = ref('')

async function save() {
  saving.value = true
  error.value = ''
  try {
    await api.patch(`/api/subscriptions/${props.sub.id}`, {
      include_keywords: form.value.include_keywords.split(/[,,\s]+/).filter(Boolean),
      exclude_keywords: form.value.exclude_keywords.split(/[,,\s]+/).filter(Boolean),
      pinned_guids: Object.keys(overrides.value).filter(g => overrides.value[g] === true),
      blocked_guids: Object.keys(overrides.value).filter(g => overrides.value[g] === false),
      exclude_batch: form.value.exclude_batch,
      backfill: form.value.backfill,
      episode_offset: Number(form.value.episode_offset) || 0,
    })
    emit('close')
  } catch (e) {
    error.value = e.message
    saving.value = false
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" style="width: min(840px, 94vw);">
      <div class="row" style="margin-bottom: 14px;">
        <h3>编辑订阅规则</h3>
        <strong>{{ sub.bangumi_title }}</strong>
        <span class="tag accent">{{ sub.subgroup_name || sub.mikan_subgroup_id }}</span>
        <div class="spacer" />
        <button class="btn sm" @click="emit('close')"><Icon name="close" :size="13" /></button>
      </div>
      <p v-if="error" style="color: var(--red); margin-bottom: 10px;">{{ error }}</p>

      <div class="form-grid">
        <label>包含关键词(全部满足,空格分隔)
          <input v-model="form.include_keywords" class="input" placeholder="如:1080 内封" />
        </label>
        <label>排除关键词(任一命中即排除)
          <input v-model="form.exclude_keywords" class="input" placeholder="如:720" />
        </label>
        <label>合集策略
          <select v-model="form.exclude_batch" class="input">
            <option :value="true">排除合集</option>
            <option :value="false">允许合集</option>
          </select>
        </label>
        <label>历史剧集
          <select v-model="form.backfill" class="input">
            <option :value="true">补齐全部历史</option>
            <option :value="false">只追新剧集</option>
          </select>
        </label>
        <label>集数偏移(跨季连续编号时自动检测,可改)
          <input v-model="form.episode_offset" class="input" type="number" min="0"
                 placeholder="如二季 25-48 → 24" />
        </label>
      </div>

      <RulePreview :bangumi-id="sub.mikan_bangumi_id" :subgroup-id="sub.mikan_subgroup_id"
                   :include="form.include_keywords" :exclude="form.exclude_keywords"
                   :exclude-batch="form.exclude_batch"
                   :overrides="overrides"
                   :eps-total="sub.bangumi_eps_total || 0"
                   @update:overrides="overrides = $event" />

      <div class="row" style="justify-content: flex-end; margin-top: 14px;">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存(自动重新评估)' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-grid label { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
</style>
