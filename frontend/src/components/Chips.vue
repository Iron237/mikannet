<script setup>
// 把解析出的标题信息渲染成一排 tag chips:集号 / 类型 / 分辨率 / 片源 / 字幕语言 / 合集 / 版本。
// 数据来自后端 _chips():{group, resolution, episode, version, is_batch, source, ep_type, subtitle_tags}
import { computed } from 'vue'

const props = defineProps({
  chips: { type: Object, required: true },
  showGroup: { type: Boolean, default: false },   // 是否显示字幕组(step3 同组,默认不显示)
})

const EP_TYPE = { special: '特别篇', credits: 'OP/ED', trailer: 'PV', other: '映像特典' }
const c = computed(() => props.chips || {})
const epType = computed(() => (c.value.ep_type && c.value.ep_type !== 'regular') ? EP_TYPE[c.value.ep_type] : null)
</script>

<template>
  <span class="chips">
    <span v-if="showGroup && c.group" class="tag">{{ c.group }}</span>
    <span v-if="epType" class="tag accent">{{ epType }}</span>
    <span v-else-if="c.episode" class="tag">{{ c.episode }}</span>
    <span v-if="c.is_batch" class="tag">合集</span>
    <span v-if="c.resolution" class="tag blue">{{ c.resolution }}</span>
    <span v-if="c.source" class="tag" :class="c.source === 'BD' ? 'accent' : ''">{{ c.source }}</span>
    <span v-for="t in (c.subtitle_tags || [])" :key="t" class="tag green">{{ t }}</span>
    <span v-if="c.version > 1" class="tag accent">v{{ c.version }}</span>
  </span>
</template>

<style scoped>
.chips { display: inline-flex; align-items: center; gap: 5px; flex-wrap: wrap; }
</style>
