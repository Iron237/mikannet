<script setup>
// BD 发行展示(去特典分支):特典不在网页展示——正片走剧集网格,特典留在发行目录里经
//  「打开目录」(mikannet://reveal)用资源管理器 / 本机应用浏览。自购原盘可逐碟 PowerDVD 蓝光播放。
import { reactive } from 'vue'
import Icon from './Icon.vue'
import { api, fmtSize } from '../api'
import { requestNative } from '../native'

const props = defineProps({
  releases: { type: Array, default: () => [] },   // 发行实体数组(含 open_url / has_discs)
  showHeader: { type: Boolean, default: true },    // 是否渲染发行标题行(BD 库页自带管理行则关)
})
const emit = defineEmits(['import'])   // 正片导入:交父级打开导入向导

const SRC_BADGE = { bdrip: ['BDRip', 'accent'], raw_disc: ['自购原盘', 'green'] }
function open(url) { if (url) requestNative(url) }

// 自购原盘:展开拉逐碟 PowerDVD(bdrip 不需要,直接「打开目录」)
const expanded = reactive({})
const discs = reactive({})
const loading = reactive({})
async function toggleDiscs(r) {
  if (expanded[r.id]) { expanded[r.id] = false; return }
  expanded[r.id] = true
  if (!discs[r.id] && !loading[r.id]) {
    loading[r.id] = true
    try { discs[r.id] = (await api.get(`/api/bd/releases/${r.id}`)).discs || [] }
    catch { discs[r.id] = [] }
    finally { loading[r.id] = false }
  }
}
</script>

<template>
  <div v-for="r in releases" :key="r.id" class="bd-rel card">
    <div v-if="showHeader" class="row" style="flex-wrap: wrap; gap: 8px;">
      <Icon name="disc" :size="16" class="muted" />
      <strong class="bd-title" :title="r.title">{{ r.title }}</strong>
      <span class="tag" :class="SRC_BADGE[r.source_kind]?.[1]">{{ SRC_BADGE[r.source_kind]?.[0] || r.source_kind }}</span>
      <span class="tag" :class="r.owned ? 'green' : 'red'">{{ r.owned ? '已购买' : '未购买' }}</span>
      <span v-if="r.total_size" class="tag">{{ fmtSize(r.total_size) }}</span>
    </div>

    <!-- 正片导入 + 打开目录:正片经向导按集号登记替换 web;特典留在发行目录里用本机应用浏览 -->
    <div class="row bd-actions">
      <button v-if="r.source_kind === 'bdrip' && r.bangumi_id" class="btn xs primary"
              title="把该发行的视频按集号登记为正片(替换 web);支持自动匹配 + 逐个手动指定"
              @click="emit('import', r)">
        <Icon name="download" :size="13" /> 正片导入
      </button>
      <span v-if="r.manual_import" class="tag green" title="已用向导手动导入正片;库扫描不再自动改这套">已手动导入</span>
      <button class="btn xs" :disabled="!r.open_url" title="在资源管理器中定位该发行目录(特典就在其中)"
              @click="open(r.open_url)">
        <Icon name="folder-open" :size="13" /> 打开目录
      </button>
      <button v-if="r.has_discs" class="btn xs" @click="toggleDiscs(r)">
        <Icon :name="expanded[r.id] ? 'chevron-down' : 'chevron-right'" :size="13" />
        {{ expanded[r.id] ? '收起' : '逐碟播放' }}
      </button>
      <span v-if="r.source_kind === 'bdrip'" class="muted bd-hint">特典(扫描 / CD / 映像特典)在发行目录内,用本机应用浏览</span>
    </div>
    <div v-if="!r.open_url" class="muted bd-hint2">
      未配置「番剧库文件夹路径」— 在 设置 → 播放 填写并安装协议处理器后可一键打开
    </div>

    <!-- 自购原盘:逐碟 PowerDVD(懒加载)-->
    <div v-if="expanded[r.id]" class="bd-discs">
      <div v-if="loading[r.id]" class="muted bd-hint">加载碟结构中…</div>
      <template v-else-if="discs[r.id]?.length">
        <div v-for="d in discs[r.id]" :key="d.name" class="bd-disc">
          <Icon name="disc" :size="14" class="muted" />
          <span class="bd-fname" :title="d.name">{{ d.name }}</span>
          <div class="spacer" />
          <button class="btn xs" :disabled="!d.bd_url" title="用 PowerDVD 蓝光播放(带菜单)" @click="open(d.bd_url)">
            <Icon name="play" :size="12" /> PowerDVD
          </button>
          <button class="btn xs" :disabled="!d.reveal_url" title="在资源管理器中打开" @click="open(d.reveal_url)">
            <Icon name="folder-open" :size="12" />
          </button>
        </div>
        <div v-if="!discs[r.id][0].bd_url" class="muted bd-hint2">
          未配置「已购原盘文件夹路径」— 在 设置 → 播放 填写并安装协议处理器后可一键播放
        </div>
      </template>
      <div v-else class="muted bd-hint">未发现可播放的碟结构(BDMV),或目录未连接。</div>
    </div>
  </div>
</template>

<style scoped>
.bd-rel { margin-bottom: 12px; padding: 13px 16px; }
.bd-title { word-break: break-all; }
.bd-actions { gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.bd-hint { font-size: 12px; }
.bd-hint2 { font-size: 12px; margin-top: 6px; }
.bd-discs { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.bd-disc { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.bd-fname { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 420px; }
</style>
