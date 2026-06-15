<script setup>
import { ref } from 'vue'
import Icon from './Icon.vue'

defineProps({ releases: { type: Array, default: () => [] } })

const lightbox = ref(null)   // 放大查看的图片 url

function fmtSize(n) {
  if (!n) return ''
  if (n >= 1073741824) return (n / 1073741824).toFixed(1) + ' GB'
  if (n >= 1048576) return (n / 1048576).toFixed(0) + ' MB'
  return (n / 1024).toFixed(0) + ' KB'
}
const SRC_BADGE = { bdrip: ['BDRip', 'accent'], raw_disc: ['自购原盘', 'green'] }
</script>

<template>
  <div v-for="r in releases" :key="r.id" class="bd-rel card">
    <div class="row" style="flex-wrap: wrap; gap: 8px;">
      <Icon name="disc" :size="16" class="muted" />
      <strong class="bd-title" :title="r.title">{{ r.title }}</strong>
      <span class="tag" :class="SRC_BADGE[r.source_kind]?.[1]">{{ SRC_BADGE[r.source_kind]?.[0] || r.source_kind }}</span>
      <span class="tag" :class="r.owned ? 'green' : 'red'">{{ r.owned ? '已购买' : '未购买' }}</span>
      <span v-if="r.disc_count > 1" class="tag">{{ r.disc_count }} 碟</span>
      <span v-if="r.total_size" class="tag">{{ fmtSize(r.total_size) }}</span>
      <span v-if="r.extra_count" class="tag">特典 {{ r.extra_count }} 项</span>
    </div>

    <div v-if="r.source_kind === 'raw_disc' && !r.groups?.length" class="muted bd-raw">
      自购原盘(BDMV)· 当前仅登记占位,m2ts 深度解析后续支持。
    </div>

    <div v-for="g in r.groups" :key="g.category" class="bd-group">
      <div class="bd-group-h">{{ g.label }} <span class="muted">{{ g.items.length }}</span></div>

      <!-- 图片(图集/扫描):缩略图网格 -->
      <div v-if="g.items[0]?.media_kind === 'image'" class="bd-gallery">
        <img v-for="it in g.items" :key="it.id" :src="it.url" loading="lazy"
             :title="it.name" @click="lightbox = it.url" />
      </div>

      <!-- 音频:逐条带播放器 -->
      <template v-else-if="g.items[0]?.media_kind === 'audio'">
        <div v-for="it in g.items" :key="it.id" class="bd-audio">
          <Icon name="volume" :size="14" class="muted" />
          <span class="bd-fname" :title="it.name">{{ it.name }}</span>
          <span class="muted sz">{{ fmtSize(it.size) }}</span>
          <audio controls preload="none" :src="it.url" />
        </div>
      </template>

      <!-- 视频/其他:逐条 + 打开链接 -->
      <template v-else>
        <a v-for="it in g.items" :key="it.id" class="bd-vid" :href="it.url" target="_blank" rel="noopener">
          <Icon name="film" :size="14" class="muted" />
          <span class="bd-fname" :title="it.name">{{ it.name }}</span>
          <span v-if="it.resolution" class="tag">{{ it.resolution }}</span>
          <span class="muted sz">{{ fmtSize(it.size) }}</span>
        </a>
      </template>
    </div>
  </div>

  <div v-if="lightbox" class="lb-mask" @click="lightbox = null">
    <img :src="lightbox" />
  </div>
</template>

<style scoped>
.bd-rel { margin-bottom: 12px; padding: 13px 16px; }
.bd-title { word-break: break-all; }
.bd-raw { font-size: 12.5px; margin-top: 8px; }
.bd-group { margin-top: 12px; }
.bd-group-h { font-size: 13px; color: var(--accent); font-weight: 600; margin-bottom: 8px; }
.bd-group-h .muted { font-weight: 400; font-size: 12px; }
.bd-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.bd-gallery img { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 6px;
  border: 1px solid var(--border); cursor: zoom-in; background: #0b0e14; }
.bd-audio { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 12.5px; flex-wrap: wrap; }
.bd-audio audio { height: 30px; margin-left: auto; max-width: 260px; }
.bd-vid { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12.5px;
  color: var(--text); text-decoration: none; padding: 4px 0; }
.bd-vid:hover { color: var(--accent); }
.bd-fname { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 420px; }
.sz { flex-shrink: 0; }
.bd-vid .sz, .bd-audio .sz { margin-left: auto; }
.lb-mask { position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 100;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; padding: 24px; }
.lb-mask img { max-width: 100%; max-height: 100%; border-radius: 8px; }
</style>
