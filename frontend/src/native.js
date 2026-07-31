// 原生启动(mikannet:// 协议)统一入口。
// Windows deploy 脚本会自动安装处理器，前端不再额外要求“我已安装”确认；
// 详情页默认用它启动本机播放器/资源管理器；网页播放与文件管理只是备用。
// url 为空表示宿主机路径尚未配置。
import { reactive } from 'vue'

const READY_KEY = 'mk_native_ready'
export const nativeState = reactive({ show: false, kind: '', pendingUrl: '' })

export function isReady() { return localStorage.getItem(READY_KEY) === '1' }
export function markReady() { localStorage.setItem(READY_KEY, '1') }

// 由播放/打开目录/PowerDVD 按钮调用
export function requestNative(url) {
  if (!url) { nativeState.kind = 'unconfigured'; nativeState.pendingUrl = ''; nativeState.show = true; return }
  window.location.href = url
}
export function launch(url) { if (url) window.location.href = url }
export function closeNative() { nativeState.show = false }
