<template>
  <n-modal :show="true" :mask-closable="false">
    <div class="pairing-modal pairing-pulse">
      <div class="pairing-header">
        <n-icon size="28" color="#F59E0B"><people-outline /></n-icon>
        <h2>{{ request.agent_name_hint }} 请求配对</h2>
      </div>
      <p class="pairing-hint">把下面的配对码告诉对方</p>
      <div class="pair-code">
        <span
          v-for="(digit, i) in digits"
          :key="i"
          class="digit digit-flip"
          :style="{ animationDelay: `${i * 80}ms` }"
        >{{ digit }}</span>
      </div>
      <p class="pair-expires">{{ timeLeft }}</p>
      <n-button type="primary" block size="large" @click="close">
        我知道了
      </n-button>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { NModal, NButton, NIcon } from 'naive-ui'
import { PeopleOutline } from '@vicons/ionicons5'
import { useMainStore } from '@/stores/main'
import type { PairRequest } from '@/types/api'

const props = defineProps<{ request: PairRequest }>()
const store = useMainStore()

const digits = computed(() => props.request.code.split(''))
const now = ref(Date.now())
const timer = setInterval(() => { now.value = Date.now() }, 1000)

const timeLeft = computed(() => {
  const expiry = new Date(props.request.expires_at).getTime()
  const diff = Math.max(0, expiry - now.value)
  const sec = Math.ceil(diff / 1000)
  return `${sec} 秒后失效`
})

function close() {
  store.dismissPairRequest()
}

onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.pairing-modal {
  background: white;
  border-radius: 20px;
  padding: 32px 24px;
  width: 90%;
  max-width: 340px;
  text-align: center;
}
.pairing-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.pairing-header h2 {
  font-size: 18px;
}
.pairing-hint {
  color: #999;
  font-size: 14px;
  margin-bottom: 20px;
}
.pair-code {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin-bottom: 12px;
}
.digit {
  width: 44px;
  height: 56px;
  line-height: 56px;
  font-size: 28px;
  font-weight: 700;
  font-family: monospace;
  background: #fef3c7;
  border-radius: 10px;
  color: #92400e;
}
.pair-expires {
  color: #999;
  font-size: 13px;
  margin-bottom: 20px;
}
</style>
