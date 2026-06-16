// 原生启动(mikanarr:// 协议)统一入口 + 「未配置/未装处理器」引导。
// 浏览器无法检测协议处理器是否已装,故用 localStorage 标记:用户在本机装好后点「我已安装」即记住,
// 之后直接唤起;未标记则先弹引导框(下载处理器)。url 为空=后端未配宿主机路径前缀。
import { reactive } from 'vue'

const READY_KEY = 'mk_native_ready'
export const nativeState = reactive({ show: false, kind: '', pendingUrl: '' })

export function isReady() { return localStorage.getItem(READY_KEY) === '1' }
export function markReady() { localStorage.setItem(READY_KEY, '1') }

// 由播放/打开目录/PowerDVD 按钮调用
export function requestNative(url) {
  if (!url) { nativeState.kind = 'unconfigured'; nativeState.pendingUrl = ''; nativeState.show = true; return }
  if (!isReady()) { nativeState.kind = 'notinstalled'; nativeState.pendingUrl = url; nativeState.show = true; return }
  window.location.href = url
}
export function launch(url) { if (url) window.location.href = url }
export function closeNative() { nativeState.show = false }
