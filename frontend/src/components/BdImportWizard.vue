<script setup>
// BD 正片导入向导:把 BD 发行里的视频按集号登记为正片(source=BD,替换 web)。
//  「自动匹配」按文件名序号预填,逐个可改集号 / 标为特典(不导入)。对该发行权威:未选为正片的
//  已登记 BD 文件从剧集网格移除(不动磁盘)。多套发行时顶部可切换。
import { computed, onMounted, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api, fmtSize } from '../api'

const props = defineProps({
  releases: { type: Array, required: true },   // [{id, title, bangumi_id, ...}](≥1)
})
const emit = defineEmits(['close', 'done'])

const sel = ref(props.releases[0]?.id)
const data = ref(null)        // { release, bangumi, files }
const rows = ref([])          // 编辑态
const loading = ref(false)
const saving = ref(false)
const err = ref('')

function initRows(files) {
  rows.value = files.map(f => ({
    path: f.path, name: f.name, folder: f.folder, size: f.size,
    guess_number: f.guess_number, guess_extra: f.guess_extra,
    current_number: f.current_number, registered: f.registered,
    current_source: f.current_source, selectable: f.selectable, origin: f.origin,
    // 预选:已登记为某集→沿用;否则按猜测(非特典且有集号才默认正片)
    main: f.selectable && (f.current_number != null
      ? f.current_source === 'BD' : (!f.guess_extra && f.guess_number != null)),
    number: f.current_number ?? f.guess_number ?? '',
  }))
}
async function load() {
  if (!sel.value) return
  loading.value = true; err.value = ''
  try {
    data.value = await api.get(`/api/bd/releases/${sel.value}/candidates`)
    initRows(data.value.files)
  } catch (e) { err.value = e.message }
  finally { loading.value = false }
}
onMounted(load)
watch(sel, load)

function autoMatch() {
  for (const r of rows.value) {
    r.main = r.selectable && !r.guess_extra && r.guess_number != null
    r.number = r.guess_number ?? ''
  }
}
function allExtra() { for (const r of rows.value) r.main = false }

const mainCount = computed(() =>
  rows.value.filter(r => r.main && r.number !== '' && r.number != null).length)

async function save() {
  const assignments = rows.value
    .filter(r => r.main && r.number !== '' && r.number != null)
    .map(r => ({ path: r.path, episode_number: Number(r.number) }))
  saving.value = true; err.value = ''
  try {
    const res = await api.post(`/api/bd/releases/${sel.value}/import`, { assignments })
    emit('done', res)
  } catch (e) { err.value = e.message; saving.value = false }
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal wiz">
      <div class="wiz-h">
        <Icon name="download" :size="16" />
        <strong>导入 BD 正片</strong>
        <span v-if="data" class="muted">→ {{ data.bangumi.title }}(共 {{ data.bangumi.eps_total ?? '?' }} 集)</span>
        <div class="spacer" />
        <button class="btn xs" title="关闭 BD 导入" aria-label="关闭 BD 导入"
                @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>

      <div v-if="releases.length > 1" class="wiz-rel">
        <span class="muted">发行:</span>
        <select class="input sm" v-model="sel">
          <option v-for="r in releases" :key="r.id" :value="r.id">{{ r.title }}</option>
        </select>
      </div>

      <div v-if="loading" class="muted wiz-body">加载发行文件中…</div>
      <div v-else-if="err" class="wiz-body" style="color: var(--red);">{{ err }}</div>
      <template v-else-if="data">
        <div class="wiz-bar">
          <button class="btn xs" @click="autoMatch"><Icon name="scan" :size="12" /> 自动匹配(按文件名)</button>
          <button class="btn xs" @click="allExtra">全设特典</button>
          <div class="spacer" />
          <span class="muted">选中正片 {{ mainCount }} · 共 {{ rows.length }} 个视频</span>
        </div>

        <div class="wiz-list">
          <div v-for="r in rows" :key="r.path" class="wiz-row" :class="{ off: !r.main }">
            <button class="role" :class="r.main ? 'primary' : ''" :disabled="!r.selectable"
                    @click="r.main = !r.main"
                    :title="!r.selectable ? '该文件不在发行目录内,也没有明确 BD 标记,不能导入'
                      : r.main ? '点为特典(不导入)' : '点为正片(导入)'">
              {{ !r.selectable ? '非 BD' : r.main ? '正片' : '特典' }}
            </button>
            <input v-if="r.main" class="input num" type="number" step="0.5" min="0"
                   v-model="r.number" placeholder="集号" />
            <span v-else class="num-spacer" />
            <span class="fname" :title="r.path">
              <span v-if="r.folder" class="ffolder">{{ r.folder }}/</span>{{ r.name }}
            </span>
            <span class="muted hint">
              <span class="tag" :class="r.selectable ? 'blue' : 'red'">{{ r.origin }}</span>
              <template v-if="r.current_number != null">当前 {{ r.current_number }} · </template>
              <template v-if="r.guess_extra">疑似特典</template>
              <template v-else-if="r.guess_number != null">猜 {{ r.guess_number }}</template>
              <template v-else>无集号</template>
              <template v-if="r.size"> · {{ fmtSize(r.size) }}</template>
            </span>
          </div>
          <div v-if="!rows.length" class="muted" style="padding: 16px;">该发行目录下没有视频文件。</div>
        </div>

        <div class="wiz-foot">
          <span class="muted">仅发行目录或已明确识别为 BD 的文件可导入;不会改标同目录中的 Web 文件。</span>
          <div class="spacer" />
          <button class="btn sm" @click="emit('close')">取消</button>
          <button class="btn sm primary" :disabled="saving" @click="save">
            {{ saving ? '导入中…' : `导入(${mainCount})` }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.wiz { width: 720px; max-width: 94vw; max-height: 88vh; display: flex; flex-direction: column; }
.wiz-h { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.wiz-rel { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.wiz-body { padding: 24px 4px; font-size: 13px; }
.wiz-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.wiz-list { overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; }
.wiz-row { display: flex; align-items: center; gap: 10px; padding: 6px 10px;
  border-bottom: 1px solid var(--border); font-size: 12.5px; }
.wiz-row:last-child { border-bottom: none; }
.wiz-row.off { opacity: .5; }
.role { width: 48px; flex-shrink: 0; padding: 3px 0; font-size: 12px; border: 1px solid var(--border);
  border-radius: 6px; background: transparent; color: var(--text); cursor: pointer; }
.role.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.role:disabled { cursor: not-allowed; opacity: .65; }
.num { width: 66px; flex-shrink: 0; padding: 3px 6px; }
.num-spacer { width: 66px; flex-shrink: 0; }
.fname { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ffolder { color: var(--text-dim); }
.hint { flex-shrink: 0; }
.wiz-foot { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
</style>
