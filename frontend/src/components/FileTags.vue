<script setup>
// 单个视频文件的标签分组展示:视频 / 音频 / 字幕 三行,加字幕组 + 体积。
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import { fmtSize } from '../api'

const props = defineProps({
  file: { type: Object, required: true },
  showPath: { type: Boolean, default: false },   // 顶部是否显示文件名/相对路径
})

// 默认只展开常用语言(简中/繁中/日语/英语),其余(多语言压制版常带几十种)折叠
const PRIMARY_SUB = /chinese|中文|简|繁|\bchs?\b|\bcht\b|\bgb\b|big5|\bzh|\bchi\b|\bzho\b|japanese|日本|日语|日語|\bjpn\b|\bja\b|\bjp\b|english|英语|英語|\beng\b|\ben\b/i
function isPrimarySub(s) {
  return PRIMARY_SUB.test(`${s.title || ''} ${s.lang || ''}`)
}
const subExpanded = ref(false)

// ffprobe 语言码(ISO 639-2)→ 中文标签;sidecar 已是中文直接用
const LANG = {
  jpn: '日语', ja: '日语', jp: '日语',
  chi: '中文', zho: '中文', chs: '简体', cht: '繁体', zh: '中文',
  eng: '英语', en: '英语', kor: '韩语', ko: '韩语',
  und: '未标', '': '未标',
}
function langLabel(code) {
  if (!code) return '未标'
  return LANG[code.toLowerCase()] || code
}
// 一条音轨:语言 + 声道 + 编码
function audioLabel(a) {
  const parts = [langLabel(a.lang)]
  if (a.channels) parts.push(a.channels)
  if (a.codec) parts.push(a.codec.toUpperCase())
  return parts.join(' ')
}
// 一条字幕轨:优先 title(常是「简日双语」),否则语言;外挂标注
function subLabel(s) {
  const base = s.title || langLabel(s.lang)
  return s.source === 'external' ? `${base}·外挂` : base
}
const f = computed(() => props.file)
const hasAudio = computed(() => (f.value.audio_tracks || []).length > 0)
const subs = computed(() => f.value.subtitle_tracks || [])
const primarySubs = computed(() => subs.value.filter(isPrimarySub))
const otherSubs = computed(() => subs.value.filter(s => !isPrimarySub(s)))
const hasSub = computed(() => subs.value.length > 0)
</script>

<template>
  <div class="ftags">
    <div v-if="showPath" class="muted path">{{ f.path || f.name }}</div>
    <!-- 整理改名前的原始文件名(保留字幕组/版本等信息) -->
    <div v-if="f.original_name && f.original_name !== f.name" class="muted orig"
         title="整理改名前的原始文件名">原始:{{ f.original_name }}</div>

    <!-- 视频:分辨率 / 片源 / 色深 / HDR / 编码 / 码率 -->
    <div class="trow">
      <Icon name="film" :size="14" class="ti" />
      <span v-if="f.resolution" class="tag blue">{{ f.resolution }}</span>
      <span v-if="f.source" class="tag" :class="f.source === 'BD' ? 'accent' : ''">{{ f.source }}</span>
      <span v-if="f.color_depth" class="tag">{{ f.color_depth }}</span>
      <span v-if="f.hdr" class="tag accent">{{ f.hdr }}</span>
      <span v-if="f.codec" class="tag">{{ f.codec.toUpperCase() }}</span>
      <span v-if="f.bitrate" class="tag">{{ (f.bitrate / 1e6).toFixed(1) }} Mbps</span>
      <span v-if="f.subgroup" class="tag"><Icon name="tag" :size="12" /> {{ f.subgroup }}</span>
      <span class="tag">{{ fmtSize(f.size) }}</span>
    </div>

    <!-- 音频:逐轨 语言+声道+编码 -->
    <div v-if="hasAudio" class="trow">
      <Icon name="volume" :size="14" class="ti" />
      <span v-for="(a, i) in f.audio_tracks" :key="'a' + i" class="tag">{{ audioLabel(a) }}</span>
    </div>

    <!-- 字幕:默认只显示 简中/繁中/日语/英语,其余折叠 -->
    <div v-if="hasSub" class="trow">
      <Icon name="captions" :size="14" class="ti" />
      <span v-for="(s, i) in primarySubs" :key="'s' + i" class="tag green">{{ subLabel(s) }}</span>
      <span v-for="(s, i) in (subExpanded ? otherSubs : [])" :key="'o' + i" class="tag green">{{ subLabel(s) }}</span>
      <button v-if="otherSubs.length" class="more" @click.stop="subExpanded = !subExpanded">
        {{ subExpanded ? '收起' : `+${otherSubs.length} 种语言` }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ftags { display: flex; flex-direction: column; gap: 5px; }
.path { font-size: 12px; word-break: break-all; }
.orig { font-size: 11.5px; word-break: break-all; opacity: .75; }
.trow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ti { color: var(--text-dim); opacity: .8; }
.tag { display: inline-flex; align-items: center; gap: 3px; }
.more {
  font-size: 11px; padding: 1px 8px; border-radius: 20px; cursor: pointer;
  border: 1px dashed var(--border); background: transparent; color: var(--text-dim);
}
.more:hover { color: var(--text); border-color: var(--accent-dim); }
</style>
