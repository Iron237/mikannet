<script setup>
defineProps({ result: Object })
</script>

<template>
  <span v-if="result" class="group-fit">
    <strong v-if="result.ongoing" class="tag blue">追更推荐</strong>
    <strong v-if="result.top" class="tag green">补番推荐</strong>
    <span v-if="result.episodes">
      {{ result.resolution }} · {{ result.language }}覆盖 {{ result.episodes }} 集，
      其中 {{ result.singles }} 集有单集发布<span v-if="result.bd">，{{ result.bd }} 集有 BD</span>
      <span v-if="result.recent_singles"> · 近 28 天单集更新 {{ result.recent_singles }} 集</span>
      <span v-if="result.latest"> · 最近匹配发布 {{ result.latest }}</span>
    </span>
    <span v-else>尚无可确认符合 {{ result.resolution }} · {{ result.language }} 的正片集数</span>
  </span>
</template>

<style scoped>
.group-fit { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: var(--text-dim); font-size: 12px; text-align: left; }
</style>
