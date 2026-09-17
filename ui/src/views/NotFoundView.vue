<script setup lang="ts">
import { NButton, NResult } from 'naive-ui'
import { RouterLink } from 'vue-router'
</script>

<template>
  <n-result status="404" title="404 — 页面不存在" description="您访问的页面不存在，请检查地址是否正确。">
    <template #footer>
      <RouterLink to="/overview" style="text-decoration: none">
        <n-button type="primary">返回首页</n-button>
      </RouterLink>
    </template>
  </n-result>
</template>
