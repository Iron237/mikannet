<script setup>
import { useRouter } from 'vue-router'
import { nativeState, markReady, launch, closeNative } from '../native'

const router = useRouter()

// 带上当前访问地址 → 安装器把它写进浏览器免询问策略,根治每次播放弹窗
function downloadHandler() { window.location.href = `/api/launch/handler.bat?origin=${encodeURIComponent(window.location.origin)}` }
function installedAndOpen() { markReady(); const u = nativeState.pendingUrl; closeNative(); launch(u) }
function tryAnyway() { const u = nativeState.pendingUrl; closeNative(); launch(u) }
function goSettings() { closeNative(); router.push('/settings') }
</script>

<template>
  <div v-if="nativeState.show" class="modal-mask" @click.self="closeNative">
    <div class="modal" style="width: 460px;">
      <template v-if="nativeState.kind === 'unconfigured'">
        <h3 style="margin-bottom: 10px;">原生播放未配置</h3>
        <p class="muted" style="font-size: 13px; line-height: 1.7;">
          要在本机用默认播放器播放 / 在资源管理器打开,需先在 <strong>设置 → 播放</strong> 填写
          <strong>文件夹路径</strong>(这台电脑看 NAS 的路径,如 <code>Z:\番剧\mikannet</code>),
          再下载并双击运行协议处理器。
        </p>
        <div class="row" style="justify-content: flex-end; margin-top: 16px;">
          <button class="btn" @click="closeNative">知道了</button>
          <button class="btn primary" @click="goSettings">去设置</button>
        </div>
      </template>
      <template v-else>
        <h3 style="margin-bottom: 10px;">需要先安装协议处理器(一次性)</h3>
        <p class="muted" style="font-size: 13px; line-height: 1.7;">
          浏览器无法直接拉起本机程序。在<strong>这台电脑</strong>上装一次协议处理器即可:
          点下面下载 <code>.bat</code> → 双击运行(会注册 <code>mikannet://</code>,无窗口闪)→ 回来点「我已安装」。
          之后点播放/打开会弹一次浏览器授权,勾「始终允许」就一劳永逸。
        </p>
        <div class="row" style="justify-content: flex-end; margin-top: 16px; flex-wrap: wrap; gap: 8px;">
          <button class="btn" @click="closeNative">取消</button>
          <button class="btn" @click="tryAnyway">仍然尝试打开</button>
          <button class="btn" @click="downloadHandler"><span>下载处理器</span></button>
          <button class="btn primary" @click="installedAndOpen">我已安装,继续</button>
        </div>
      </template>
    </div>
  </div>
</template>
