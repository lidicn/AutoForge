<script setup lang="ts">
import { ref, watch } from 'vue'
import { ExternalLink, Pencil, Check, KeyRound, X } from 'lucide-vue-next'
import { api } from '../api'
import { useAgentsStore } from '../stores/agents'
import { useAuthCodesStore } from '../stores/authCodes'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
const agentsStore = useAgentsStore()
const authCodesStore = useAuthCodesStore()
const DEV_PANEL_URL = import.meta.env.VITE_DEV_PANEL_URL || '/ui'

const editing = ref<string | null>(null)
const draft = ref('')

// owner 侧「暂停接受配对请求」（裁定 20261006 §一）
const accepting = ref(true)
const acceptingKnown = ref(false)
const acceptingBusy = ref(false)
const acceptingError = ref('')

async function loadAccepting() {
  acceptingError.value = ''
  try {
    accepting.value = (await api.getPairAccepting()).accepting
    acceptingKnown.value = true
  } catch (e) {
    // 读不到就不把开关画成任何一个确定档位：显示"未知"比显示一个没人核实的值诚实
    acceptingKnown.value = false
    acceptingError.value = '读不到配对开关：' + (e as Error).message
  }
}

async function toggleAccepting() {
  acceptingBusy.value = true
  acceptingError.value = ''
  try {
    accepting.value = (await api.setPairAccepting(!accepting.value)).accepting
    acceptingKnown.value = true
  } catch (e) {
    acceptingError.value = '切换失败，当前状态未变：' + (e as Error).message
  } finally {
    acceptingBusy.value = false
  }
}

watch(
  () => props.open,
  (v) => {
    if (v) {
      agentsStore.load()
      authCodesStore.load()
      loadAccepting()
    }
  },
)

function startEdit(id: string, name: string) {
  editing.value = id
  draft.value = name
}
async function saveEdit(id: string) {
  if (!draft.value.trim()) return
  await agentsStore.rename(id, draft.value.trim())
  editing.value = null
}
function goAuth() {
  emit('update:open', false)
  location.hash = '#/auth-codes'
}
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    /* 忽略 */
  }
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-30">
    <div class="absolute inset-0 bg-black/40 animate-pop-in" @click="emit('update:open', false)" />
    <aside
      class="absolute right-0 top-0 bottom-0 w-[88%] max-w-sm bg-white shadow-card flex flex-col animate-pop-in"
    >
      <header class="flex items-center justify-between px-4 h-14 border-b border-black/5">
        <span class="font-semibold text-ink">设置</span>
        <button class="text-ink-faint hover:text-ink" @click="emit('update:open', false)">
          <X :size="20" />
        </button>
      </header>

      <div class="flex-1 overflow-y-auto no-scrollbar p-4 space-y-6">
        <!-- 给 Agent 改名 -->
        <section>
          <h3 class="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-2">给 Agent 改名</h3>
          <div v-if="agentsStore.loading" class="text-sm text-ink-faint">加载中…</div>
          <div v-else-if="!agentsStore.agents.length" class="text-sm text-ink-faint">暂无已配对 Agent</div>
          <ul v-else class="space-y-2">
            <li v-for="a in agentsStore.agents" :key="a.id" class="rounded-xl border border-black/5 p-3">
              <div v-if="editing === a.id" class="flex items-center gap-2">
                <input
                  v-model="draft"
                  class="flex-1 px-3 py-1.5 rounded-lg border border-forge-300 text-sm outline-none focus:ring-2 focus:ring-forge-200"
                  @keyup.enter="saveEdit(a.id)"
                />
                <button
                  class="grid place-items-center w-8 h-8 rounded-lg bg-forge-500 text-white active:scale-95"
                  @click="saveEdit(a.id)"
                >
                  <Check :size="16" />
                </button>
              </div>
              <div v-else class="flex items-center justify-between">
                <div class="min-w-0">
                  <div class="text-sm font-medium text-ink truncate">{{ a.name }}</div>
                  <div class="text-[11px] text-ink-faint">{{ a.scopes.join(' / ') }}</div>
                </div>
                <button class="text-ink-faint hover:text-forge-600" @click="startEdit(a.id, a.name)">
                  <Pencil :size="16" />
                </button>
              </div>
            </li>
          </ul>
        </section>

        <!-- 配对请求开关（owner 止血口，裁定 20261006 §一） -->
        <section>
          <h3 class="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-2">配对请求</h3>
          <div class="rounded-xl border border-black/5 p-3">
            <div class="text-sm font-medium text-ink">
              {{
                !acceptingKnown
                  ? '开关状态未读到'
                  : accepting
                    ? '正在接受新的配对请求'
                    : '已暂停接受配对请求'
              }}
            </div>
            <p class="text-[11px] text-ink-faint mt-1 leading-relaxed">
              暂停后，Agent 发起配对会被直接拒绝，弹窗不再打扰；要重新配对请回到这里恢复。
            </p>
            <button
              class="mt-3 w-full rounded-lg px-3 py-2 text-sm font-medium transition active:scale-95 disabled:opacity-50"
              :class="
                accepting && acceptingKnown ? 'bg-black/5 text-ink hover:bg-black/10' : 'bg-forge-500 text-white'
              "
              :disabled="acceptingBusy"
              @click="acceptingKnown ? toggleAccepting() : loadAccepting()"
            >
              {{
                !acceptingKnown ? '重试读取开关状态' : accepting ? '暂停接受配对请求' : '恢复接受配对请求'
              }}
            </button>
            <p v-if="acceptingError" class="text-[11px] text-red-600 mt-2">{{ acceptingError }}</p>
          </div>
        </section>

        <!-- 跳转开发面板（恒显） -->
        <section>
          <h3 class="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-2">开发面板</h3>
          <a
            :href="DEV_PANEL_URL"
            target="_blank"
            rel="noopener"
            class="flex items-center justify-between rounded-xl border border-black/5 p-3 hover:bg-black/5 transition"
          >
            <span class="text-sm text-ink">跳转开发面板</span>
            <ExternalLink :size="16" class="text-ink-faint" />
          </a>
        </section>

        <!-- 授权码管理 -->
        <section>
          <div class="flex items-center justify-between mb-2">
            <h3 class="text-xs font-semibold text-ink-faint uppercase tracking-wide">长期授权码</h3>
            <button class="text-xs text-forge-600 font-medium" @click="goAuth">前往管理</button>
          </div>
          <ul v-if="authCodesStore.codes.length" class="space-y-2">
            <li
              v-for="c in authCodesStore.codes.filter((x) => x.kind === 'long')"
              :key="c.code"
              class="flex items-center justify-between rounded-xl border border-black/5 p-3"
            >
              <div class="flex items-center gap-2">
                <KeyRound :size="16" class="text-forge-600" />
                <span class="font-mono text-sm tracking-widest text-ink">{{ c.code }}</span>
              </div>
              <button class="text-[11px] text-ink-faint hover:text-ink" @click="copy(c.code)">复制</button>
            </li>
          </ul>
          <p v-else class="text-sm text-ink-faint">暂无授权码</p>
        </section>
      </div>
    </aside>
  </div>
</template>
