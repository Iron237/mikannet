export function pollSummary(result) {
  if (result.error) return `检查失败 · ${result.error}`
  if (result.note && !('seen' in result)) return result.note
  const parts = [result.note || `读取 ${result.seen} 条`]
  if (result.history) parts.push(`历史 ${result.history} 条`)
  if (result.submitted) parts.push(`已提交 ${result.submitted} 条`)
  if (result.failed) parts.push(`提交失败 ${result.failed} 条`)
  if (result.skipped) parts.push(`规则跳过 ${result.skipped} 条`)
  if (!result.submitted && !result.failed && !result.skipped && !result.note) parts.push('没有新增任务')
  return parts.join(' · ')
}

export function needsAttention(sub) {
  return sub.source === 'rss' && (!sub.last_poll_ok || sub.last_poll_summary?.failed > 0)
}
