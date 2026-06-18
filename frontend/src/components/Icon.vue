<script setup>
// 统一图标:内嵌 SVG 路径(24×24,描边 currentColor),零依赖,替代所有 emoji。
// 风格对齐 Lucide/Tabler(MIT):圆角描边、stroke-width 1.8。实心图标(play/star/zap)单独填充。
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  // 描边粗细可微调
  stroke: { type: [Number, String], default: 1.8 },
})

// 每个图标 = 内部 SVG 标记(受控常量,非用户输入 → v-html 安全)
const ICONS = {
  library: '<rect x="3" y="4" width="6" height="16" rx="1"/><rect x="11" y="4" width="6" height="16" rx="1"/><path d="M19.5 5.2l1.8 14.6"/>',
  search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.6" y2="16.6"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 9.5h18"/><path d="M8 3v4M16 3v4"/>',
  rss: '<path d="M5 11a9 9 0 0 1 9 9"/><path d="M5 5a15 15 0 0 1 15 15"/><circle cx="6" cy="19" r="1.4" fill="currentColor" stroke="none"/>',
  download: '<path d="M12 3v12"/><path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M5 20h14"/>',
  logs: '<path d="M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M8 9h8M8 13h8M8 17h5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M21.5 12h-3M5.5 12h-3M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1M18.4 18.4l-2.1-2.1M7.7 7.7L5.6 5.6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  'folder-in': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 10.5v4.5M9.5 12.5L12 15l2.5-2.5"/>',
  scan: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><line x1="4.5" y1="12" x2="19.5" y2="12"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z" fill="currentColor" stroke="none"/>',
  alert: '<path d="M12 3.2L21.5 20H2.5z"/><line x1="12" y1="9.5" x2="12" y2="14"/><line x1="12" y1="17" x2="12" y2="17"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6.5 7l.9 12.1A2 2 0 0 0 9.4 21h5.2a2 2 0 0 0 2-1.9L17.5 7"/><path d="M10 11v6M14 11v6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  'arrow-left': '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  tag: '<path d="M11 3H5a2 2 0 0 0-2 2v6l9.5 9.5a1.5 1.5 0 0 0 2.1 0l5.9-5.9a1.5 1.5 0 0 0 0-2.1L11 3z"/><circle cx="7.5" cy="7.5" r="1.3"/>',
  volume: '<path d="M4 9.5v5h3.5L13 19V5L7.5 9.5z"/><path d="M16 9.5a3.5 3.5 0 0 1 0 5"/>',
  captions: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7.5 10.5h3M7.5 14h6M13.5 10.5h3"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  pause: '<rect x="6.5" y="5" width="3.5" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="3.5" height="14" rx="1" fill="currentColor" stroke="none"/>',
  play: '<path d="M7 4.5l13 7.5-13 7.5z" fill="currentColor" stroke="none"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-.6 4"/><path d="M20 5v6h-6"/>',
  star: '<path d="M12 3.2l2.6 5.7 6.2.7-4.6 4.2 1.2 6.1L12 16.9 6.6 19.9l1.2-6.1L3.2 9.6l6.2-.7z" fill="currentColor" stroke="none"/>',
  tv: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 3l4 4 4-4"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7.5 4v16M16.5 4v16M3 9h4.5M3 15h4.5M16.5 9H21M16.5 15H21"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.4"/>',
  edit: '<path d="M4 20h4l10-10-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  link: '<path d="M9 15l6-6"/><path d="M10.5 6.5l1.8-1.8a3.5 3.5 0 0 1 5 5L14.5 12"/><path d="M13.5 17.5l-1.8 1.8a3.5 3.5 0 0 1-5-5L9.5 12"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M19 13.5V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19V8a1.5 1.5 0 0 1 1.5-1.5H12"/>',
  'folder-open': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v1H6.5a2 2 0 0 0-1.9 1.4L3 17z"/><path d="M3 17l1.6-5.6A2 2 0 0 1 6.5 10H21l-2 7a1 1 0 0 1-1 0.8H4a1 1 0 0 1-1-1z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  database: '<ellipse cx="12" cy="5.5" rx="7.5" ry="3"/><path d="M4.5 5.5v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-12"/><path d="M4.5 11.5c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
  'chevron-right': '<path d="M9 6l6 6-6 6"/>',
  'chevron-left': '<path d="M15 6l-6 6 6 6"/>',
  'chevron-down': '<path d="M6 9l6 6 6-6"/>',
}

const body = computed(() => ICONS[props.name] || '')
</script>

<template>
  <svg class="icon" :width="size" :height="size" viewBox="0 0 24 24"
       fill="none" :stroke-width="stroke" stroke="currentColor"
       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" v-html="body" />
</template>

<style scoped>
.icon { display: inline-block; vertical-align: -0.16em; flex-shrink: 0; }
</style>
