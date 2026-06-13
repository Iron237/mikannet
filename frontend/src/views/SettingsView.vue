<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const health = ref(null)
const channels = ref([])
const saving = ref('')
const testResult = ref({})

const channelMeta = {
  telegram: { name: 'Telegram Bot', fields: [['bot_token', 'Bot Token'], ['chat_id', 'Chat ID']] },
  serverchan: { name: 'Server酱', fields: [['send_key', 'SendKey (SCT…)']] },
  pushplus: { name: 'PushPlus', fields: [['token', 'Token']] },
}
const eventLabels = { on_new: '检测到更新', on_start: '开始下载', on_complete: '下载完成', on_fail: '下载失败' }

async function load() {
  health.value = await api.get('/api/system/health')
  channels.value = await api.get('/api/notifications')
}

async function save(ch) {
  saving.value = ch.channel
  try {
    await api.put(`/api/notifications/${ch.channel}`, ch)
  } finally { saving.value = '' }
}

async function test(ch) {
  testResult.value = { ...testResult.value, [ch.channel]: '发送中…' }
  try {
    await save(ch)
    await api.post(`/api/notifications/${ch.channel}/test`)
    testResult.value = { ...testResult.value, [ch.channel]: '✅ 发送成功' }
  } catch (e) {
    testResult.value = { ...testResult.value, [ch.channel]: '❌ ' + e.message }
  }
}

async function pollNow() {
  await api.post('/api/system/poll')
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-title">设置</div>

    <div class="card" style="margin-bottom: 14px;">
      <h3 style="margin-bottom: 10px;">系统状态</h3>
      <div v-if="health" class="row">
        <span class="tag" :class="health.status === 'ok' ? 'green' : 'red'">
          qBittorrent {{ health.status === 'ok' ? '已连接' : '不可达' }}
        </span>
        <span class="muted" v-if="health.qbittorrent">
          {{ health.qbittorrent.version }} / API {{ health.qbittorrent.api }}
        </span>
        <div class="spacer" />
        <button class="btn sm" @click="pollNow">立即检查订阅更新</button>
      </div>
    </div>

    <h3 style="margin: 20px 0 10px; font-size: 15px;">推送通知</h3>
    <div v-for="ch in channels" :key="ch.channel" class="card" style="margin-bottom: 12px;">
      <div class="row" style="margin-bottom: 12px;">
        <strong>{{ channelMeta[ch.channel]?.name ?? ch.channel }}</strong>
        <label class="row" style="cursor: pointer; gap: 6px;">
          <input type="checkbox" v-model="ch.enabled" /> 启用
        </label>
        <label v-if="ch.channel === 'telegram'" class="row" style="cursor: pointer; gap: 6px;">
          <input type="checkbox" v-model="ch.use_proxy" /> 走代理
        </label>
        <div class="spacer" />
        <span class="muted" style="font-size: 12px;">{{ testResult[ch.channel] }}</span>
        <button class="btn sm" @click="test(ch)">测试推送</button>
        <button class="btn sm primary" :disabled="saving === ch.channel" @click="save(ch)">
          {{ saving === ch.channel ? '保存中…' : '保存' }}
        </button>
      </div>
      <div class="cred-grid">
        <label v-for="[key, label] in channelMeta[ch.channel]?.fields ?? []" :key="key">
          {{ label }}
          <input v-model="ch.credentials[key]" class="input" :placeholder="label"
                 :type="key.includes('token') || key.includes('key') ? 'password' : 'text'" />
        </label>
      </div>
      <div class="row" style="margin-top: 12px; flex-wrap: wrap;">
        <span class="muted" style="font-size: 12.5px;">推送事件:</span>
        <label v-for="(label, ev) in eventLabels" :key="ev" class="row" style="cursor: pointer; gap: 5px;">
          <input type="checkbox" v-model="ch.events[ev]" /> {{ label }}
        </label>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cred-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.cred-grid label { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 5px; }
</style>
