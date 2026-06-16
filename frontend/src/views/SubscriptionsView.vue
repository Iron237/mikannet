<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import EditSubscriptionModal from '../components/EditSubscriptionModal.vue'
import LocalImportModal from '../components/LocalImportModal.vue'
import SubscribeWizard from '../components/SubscribeWizard.vue'
import Icon from '../components/Icon.vue'

const SRC = { auto: '智能下载', local: '本地导入' }
const subs = ref([])
const showWizard = ref(false)
const editing = ref(null)
const showImport = ref(false)
const showLocalImport = ref(false)
const importUrl = ref('')
const importBackfill = ref(false)
const importStatus = ref(null)
const importError = ref('')
const showImportAll = ref(false)
const cookieInput = ref('')
const sinceYear = ref('')
const sinceSeason = ref('')
const untilYear = ref('')
const untilSeason = ref('')
const importAllAutoDl = ref(false)
const allStatus = ref(null)
const allError = ref('')
let allTimer = null
const selected = ref(new Set())
const delConfirm = ref(null)     // { ids: [...] } 待确认删除
const delFiles = ref(false)
const busy = ref(false)
let pollTimer = null

const allSelected = computed(() => subs.value.length > 0 && selected.value.size === subs.value.length)

function isSel(id) { return selected.value.has(id) }
function toggleSel(id) {
  const s = new Set(selected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selected.value = s
}
function selectAll() { selected.value = new Set(subs.value.map(s => s.id)) }
function clearSel() { selected.value = new Set() }

async function load() {
  subs.value = await api.get('/api/subscriptions')
  const ids = new Set(subs.value.map(s => s.id))
  selected.value = new Set([...selected.value].filter(id => ids.has(id)))
}

async function toggle(sub) {
  await api.patch(`/api/subscriptions/${sub.id}`, { enabled: !sub.enabled })
  await load()
}

async function doDelete() {
  busy.value = true
  try {
    await api.post('/api/subscriptions/batch-delete',
      { ids: delConfirm.value.ids, delete_files: delFiles.value })
    delConfirm.value = null; delFiles.value = false; clearSel()
    await load()
  } finally { busy.value = false }
}

async function startImportAll() {
  allError.value = ''
  try {
    await api.post('/api/import/mikan-all', {
      cookie: cookieInput.value.trim(),
      since_year: sinceYear.value || null, since_season: sinceSeason.value || '',
      until_year: untilYear.value || null, until_season: untilSeason.value || '',
      auto_download: importAllAutoDl.value,
    })
    pollImportAll()
  } catch (e) { allError.value = e.message }
}
async function pollImportAll() {
  allStatus.value = await api.get('/api/import/mikan-all/status')
  await load()
  if (allStatus.value.running) allTimer = setTimeout(pollImportAll, 2000)
}

async function startImport() {
  importError.value = ''
  try {
    await api.post('/api/import/mikan', { rss_url: importUrl.value, backfill: importBackfill.value })
    pollImport()
  } catch (e) { importError.value = e.message }
}

async function pollImport() {
  importStatus.value = await api.get('/api/import/mikan/status')
  await load()
  if (importStatus.value.running) {
    pollTimer = setTimeout(pollImport, 2000)
  }
}

onMounted(load)
onUnmounted(() => { clearTimeout(pollTimer); clearTimeout(allTimer) })
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 18px;">
      <div class="page-title" style="margin: 0;">订阅管理</div>
      <div class="spacer" />
      <button v-if="subs.length" class="btn sm" @click="allSelected ? clearSel() : selectAll()">
        {{ allSelected ? '取消全选' : '全选' }}
      </button>
      <button v-if="subs.length" class="btn sm" :disabled="!selected.size" @click="clearSel">清空选择</button>
      <button class="btn" @click="showLocalImport = true"><Icon name="folder" :size="14" /> 导入本地番剧</button>
      <button class="btn" @click="showImportAll = true"><Icon name="folder-in" :size="14" /> 蜜柑订阅入库</button>
      <button class="btn" @click="showImport = true"><Icon name="download" :size="14" /> 导入蜜柑订阅</button>
      <button class="btn primary" @click="showWizard = true"><Icon name="plus" :size="14" /> 添加订阅</button>
    </div>

    <div v-if="selected.size" class="batch-bar card">
      <strong>已选 {{ selected.size }} 个订阅</strong>
      <div class="spacer" />
      <button class="btn sm danger" :disabled="busy" @click="delConfirm = { ids: [...selected] }">
        <Icon name="trash" :size="13" /> 批量删除
      </button>
    </div>

    <div v-if="!subs.length" class="card" style="text-align: center; padding: 50px; color: var(--text-dim);">
      还没有订阅 — 点击右上角「添加订阅」搜索蜜柑番剧
    </div>

    <div v-for="s in subs" :key="s.id" class="card sub-row" :class="{ sel: isSel(s.id) }">
      <div class="row">
        <input type="checkbox" class="ck" :checked="isSel(s.id)" @change="toggleSel(s.id)" />
        <span v-if="s.source === 'rss'" class="health" :class="s.last_poll_ok ? 'ok' : 'bad'"
              :title="s.last_poll_error || 'RSS 正常'">●</span>
        <Icon v-else :name="s.source === 'auto' ? 'zap' : 'folder'" :size="15" class="muted" />
        <div>
          <div style="font-weight: 600;">
            <RouterLink :to="`/bangumi/${s.bangumi_id}`" class="b-link">{{ s.bangumi_title }}</RouterLink>
            <span v-if="s.source !== 'rss'" class="tag" style="margin-left: 8px;">{{ SRC[s.source] }}</span>
          </div>
          <div class="muted" style="font-size: 12.5px; margin-top: 2px;">
            <template v-if="s.source === 'rss'">
              {{ s.subgroup_name || '字幕组 #' + s.mikan_subgroup_id }}
              <span v-if="s.include_keywords.length"> · 包含: {{ s.include_keywords.join(' + ') }}</span>
              <span v-if="s.exclude_keywords.length"> · 排除: {{ s.exclude_keywords.join(' / ') }}</span>
              <span> · {{ s.exclude_batch ? '排除合集' : '允许合集' }}</span>
              <span> · {{ s.backfill ? '补齐历史' : '只追新' }}</span>
            </template>
            <template v-else>
              {{ s.source === 'auto' ? '智能下载补全的源(随番剧库扫描自动维护)' : '本地导入的文件容器' }}
            </template>
          </div>
        </div>
        <div class="spacer" />
        <template v-if="s.source === 'rss'">
          <span class="muted" style="font-size: 12px;" v-if="s.last_checked_at">
            上次检查 {{ new Date(s.last_checked_at + 'Z').toLocaleString('zh-CN') }}
          </span>
          <button class="btn sm" @click="editing = s"><Icon name="edit" :size="13" /> 编辑规则</button>
          <button class="btn sm" :class="{ primary: s.enabled }" @click="toggle(s)">
            {{ s.enabled ? '已启用' : '已停用' }}
          </button>
        </template>
        <RouterLink v-else class="btn sm" :to="`/bangumi/${s.bangumi_id}`"><Icon name="library" :size="13" /> 查看</RouterLink>
        <button class="btn sm danger" @click="delConfirm = { ids: [s.id] }"><Icon name="trash" :size="13" /> 删除</button>
      </div>
    </div>

    <div v-if="delConfirm" class="modal-mask" @click.self="delConfirm = null">
      <div class="modal" style="width: 440px;">
        <h3 style="margin-bottom: 10px;">删除 {{ delConfirm.ids.length }} 个订阅</h3>
        <p class="muted" style="font-size: 12.5px;">
          将移除订阅及其下载任务记录(番剧与剧集保留)。
        </p>
        <label class="row" style="margin: 16px 0; cursor: pointer;">
          <input type="checkbox" v-model="delFiles" />
          同时删除已下载的文件
        </label>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="delConfirm = null">取消</button>
          <button class="btn danger" :disabled="busy" @click="doDelete">确认删除</button>
        </div>
      </div>
    </div>

    <div v-if="showImportAll" class="modal-mask" @click.self="showImportAll = false">
      <div class="modal" style="width: 580px;">
        <h3 style="margin-bottom: 8px;">导入蜜柑全部订阅到番剧库(含历史季度)</h3>
        <p class="muted" style="font-size: 12.5px; margin-bottom: 12px;">
          用你的蜜柑登录 cookie 抓「我的番组」,遍历季度(可用下方<strong>年份范围</strong>限定),
          把订阅过的番剧加入番剧库(补元数据,已在库的跳过)。默认<strong>只入库不下载</strong>;
          需要的话勾选下方在入库后<strong>智能下载补齐</strong>。
          cookie 会存进设置(打码),过期后重新粘贴即可。<br>
          取法:登录 mikanani.me → F12 → 网络 → 任意请求的 <code>Cookie</code> 整行;
          或 应用→Cookies 里 <code>.AspNetCore.Identity.Application</code> 的值。
        </p>
        <textarea v-model="cookieInput" class="input" rows="3"
                  placeholder="粘贴 cookie(留空则用已保存的)"></textarea>
        <div class="row" style="margin-top: 10px; gap: 6px; flex-wrap: wrap; align-items: center;">
          <span class="muted" style="font-size: 12.5px;">时间范围(季度,留空=全部)</span>
          <input v-model="sinceYear" type="number" class="input" style="width: 80px;" placeholder="起始年" />
          <select v-model="sinceSeason" class="input" style="width: 64px;">
            <option value="">季</option><option>冬</option><option>春</option><option>夏</option><option>秋</option>
          </select>
          <span class="muted">~</span>
          <input v-model="untilYear" type="number" class="input" style="width: 80px;" placeholder="结束年" />
          <select v-model="untilSeason" class="input" style="width: 64px;">
            <option value="">季</option><option>冬</option><option>春</option><option>夏</option><option>秋</option>
          </select>
        </div>
        <label class="row" style="margin-top: 10px; cursor: pointer; font-size: 12.5px; gap: 6px;">
          <input type="checkbox" v-model="importAllAutoDl" />
          导入后对范围内新番剧<b>智能下载补齐</b>(按画质关卡挑最优源,进度见番剧库)
        </label>
        <p v-if="allError" style="color: var(--red); font-size: 12.5px; margin-top: 8px;">{{ allError }}</p>
        <div v-if="allStatus" class="card" style="margin: 10px 0; padding: 12px; font-size: 12.5px;">
          <div>{{ allStatus.running ? allStatus.phase + '…' : '完成' }}
            {{ allStatus.done }}/{{ allStatus.total }} ·
            入库 {{ allStatus.created.length }} · 跳过 {{ allStatus.skipped }}
            <span v-if="allStatus.errors"> · 失败 {{ allStatus.errors }}</span>
          </div>
          <div v-if="allStatus.error" style="color: var(--red);">{{ allStatus.error }}</div>
          <div v-for="c in allStatus.created" :key="c" class="muted"><Icon name="check" :size="12" /> {{ c }}</div>
        </div>
        <div class="row" style="justify-content: flex-end; margin-top: 10px;">
          <button class="btn" @click="showImportAll = false">关闭</button>
          <button class="btn primary" :disabled="allStatus?.running" @click="startImportAll">
            {{ allStatus?.running ? '导入中…' : '开始导入' }}
          </button>
        </div>
      </div>
    </div>

    <SubscribeWizard v-if="showWizard" @close="showWizard = false; load()" />
    <EditSubscriptionModal v-if="editing" :sub="editing" @close="editing = null; load()" />
    <LocalImportModal v-if="showLocalImport" @close="showLocalImport = false; load()" />

    <div v-if="showImport" class="modal-mask" @click.self="showImport = false">
      <div class="modal" style="width: 540px;">
        <h3 style="margin-bottom: 8px;">导入蜜柑订阅</h3>
        <p class="muted" style="font-size: 12.5px; margin-bottom: 14px;">
          登录 mikanani.me → 首页「RSS 订阅」拿到个人聚合链接(形如
          <code>https://mikanani.me/RSS/MyBangumi?token=…</code>),粘贴到下面。
          系统会反查每个条目对应的番剧和字幕组,自动批量建订阅。
        </p>
        <input v-model="importUrl" class="input" placeholder="https://mikanani.me/RSS/MyBangumi?token=…" />
        <label class="row" style="margin: 12px 0; cursor: pointer; font-size: 13px;">
          <input type="checkbox" v-model="importBackfill" />
          导入的订阅补齐全部历史剧集(不勾 = 只追新,推荐)
        </label>
        <p v-if="importError" style="color: var(--red); font-size: 12.5px;">{{ importError }}</p>
        <div v-if="importStatus" class="card" style="margin: 10px 0; padding: 12px; font-size: 12.5px;">
          <div>{{ importStatus.running ? '导入中…' : '导入完成' }}
            进度 {{ importStatus.done }}/{{ importStatus.total }},
            新建 {{ importStatus.created.length }} 个订阅
            <span v-if="importStatus.errors">,失败 {{ importStatus.errors }}</span>
          </div>
          <div v-for="c in importStatus.created" :key="c" class="muted"><Icon name="check" :size="12" /> {{ c }}</div>
        </div>
        <div class="row" style="justify-content: flex-end;">
          <button class="btn" @click="showImport = false">关闭</button>
          <button class="btn primary" :disabled="importStatus?.running" @click="startImport">
            {{ importStatus?.running ? '导入中…' : '开始导入' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub-row { margin-bottom: 10px; padding: 14px 18px; }
.sub-row.sel { border-color: var(--accent); }
.ck { accent-color: var(--accent); flex-shrink: 0; cursor: pointer; }
.health { font-size: 10px; }
.health.ok { color: var(--green); }
.health.bad { color: var(--red); }
.b-link { color: var(--text); text-decoration: none; }
.b-link:hover { color: var(--accent); }
.batch-bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 16px; margin-bottom: 12px;
  position: sticky; top: 8px; z-index: 10; border-color: var(--accent-dim);
}
</style>
