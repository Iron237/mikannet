<script setup>
import { computed, ref, watch } from 'vue'
import { requestNative } from '../native'
import Icon from './Icon.vue'

const props = defineProps({
  file: { type: Object, default: null },
})
const emit = defineEmits(['close'])
const compatible = ref(false)
const playError = ref('')
const source = computed(() => compatible.value
  ? props.file?.compatible_stream_url
  : props.file?.stream_url)

watch(() => props.file, () => {
  compatible.value = false
  playError.value = ''
})

function useCompatible() {
  compatible.value = true
  playError.value = ''
}
</script>

<template>
  <div v-if="file" class="modal-mask" @click.self="emit('close')">
    <div class="modal player-modal">
      <div class="row player-head">
        <div>
          <h3>网页播放</h3>
          <div class="muted file-name">{{ file.name || file.path }}</div>
        </div>
        <div class="spacer" />
        <button class="btn icon-only" aria-label="关闭播放器" @click="emit('close')">×</button>
      </div>

      <video :key="source" class="web-player" :src="source" controls autoplay
             @error="playError = compatible
               ? '兼容转码启动失败，请检查文件或 ffmpeg 日志。'
               : '浏览器无法直接解码该文件，可切换兼容转码。'" />

      <div class="row player-actions">
        <span class="tag" :class="compatible ? 'accent' : 'green'">
          {{ compatible ? '兼容转码 · H.264/AAC' : '原画直连 · 支持拖动' }}
        </span>
        <span v-if="playError" class="player-error">{{ playError }}</span>
        <div class="spacer" />
        <button v-if="!compatible" class="btn sm" @click="useCompatible">
          <Icon name="refresh" :size="13" /> 兼容播放
        </button>
        <button v-if="file.play_url" class="btn sm" @click="requestNative(file.play_url)">
          <Icon name="external" :size="13" /> 本机播放器
        </button>
      </div>
      <p class="muted player-note">
        原画模式直接从媒体库读取，支持 HTTP Range。MKV、HEVC、特殊音轨在浏览器不兼容时，
        “兼容播放”会由服务器实时转码，首次起播可能需要数秒且会占用 CPU。
      </p>
    </div>
  </div>
</template>

<style scoped>
.player-modal { width: min(960px, calc(100vw - 28px)); }
.player-head { margin-bottom: 12px; }
.player-head h3 { margin: 0 0 4px; }
.file-name { font-size: 12px; max-width: 720px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.web-player { display: block; width: 100%; max-height: 68vh; min-height: 260px; background: #000; border-radius: 8px; }
.player-actions { gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.player-error { color: var(--red); font-size: 12px; }
.player-note { font-size: 12px; line-height: 1.6; margin: 10px 0 0; }
.icon-only { font-size: 22px; line-height: 1; padding: 3px 9px; }
@media (max-width: 640px) {
  .web-player { min-height: 190px; }
}
</style>
