import type { Automation, DeviceRef, TrialInfo, TrialState } from '../types/api.ts'

const collator = new Intl.Collator('zh-Hans-CN')

// ---------- 状态标签（未知值 fail-open 兜底） ----------
export type Tone = 'success' | 'warning' | 'error' | 'neutral'
export type TagType = 'success' | 'warning' | 'error' | 'default'

export interface StatusMeta { label: string; tone: Tone; tagType: TagType }

const STATUS_TABLE: Record<string, StatusMeta> = {
  pending:  { label: '待批',   tone: 'warning', tagType: 'warning' },
  enabled:  { label: '已启用', tone: 'success', tagType: 'success' },
  disabled: { label: '已停用', tone: 'neutral', tagType: 'default' },
  anomaly:  { label: '异常',   tone: 'error',   tagType: 'error' },
}

export function statusMeta (status: string): StatusMeta {
  return STATUS_TABLE[status] ?? { label: String(status), tone: 'neutral', tagType: 'default' }
}

// ---------- 试演期状态条 ----------
export interface TrialStep { key: TrialState; label: string; hint: string }

export const TRIAL_STEPS: TrialStep[] = [
  { key: 'shadow', label: '影子', hint: '只记录不执行' },
  { key: 'canary', label: '金丝雀', hint: '小范围试执行' },
  { key: 'auto',   label: '自动', hint: '全自动执行' },
]

export interface TrialMeta {
  available: boolean
  steps: TrialStep[]
  activeIndex: number
  stateLabel: string | null
  anomaly: string | null
  since: string | null
  until: string | null
  level: 'normal' | 'anomaly'
}

/** 后端运行事实 → UI 三段放权某一步（⛔ 不改后端词表，映射只活在这一层） */
const TRIAL_STATE_TO_STEP: Record<string, TrialState> = {
  observing: 'canary',
  verified: 'auto',
}

/** 落不到条上的事实（回滚/失败）用显式文案，⛔ 不给用户一条全灰的条当答案 */
const TRIAL_STATE_LABEL: Record<string, string> = {
  observing: '观察中',
  verified: '已转正',
  rolled_back: '已回滚',
  failed: '试演失败',
  // 2026-11-05 AF-Core D8：动作无预期态映射时会落 unmodeled（不是假绿成 verified），
  // 界面必须给一行明确文案，⛔ 不许让试演条空着让用户猜。
  unmodeled: '无预期态对照',
}

export function trialMeta (trial: TrialInfo | null): TrialMeta {
  if (!trial) {
    return { available: false, steps: TRIAL_STEPS, activeIndex: -1, stateLabel: null, anomaly: null, since: null, until: null, level: 'normal' }
  }
  const stepKey = TRIAL_STATE_TO_STEP[trial.state] ?? trial.state
  const activeIndex = TRIAL_STEPS.findIndex(s => s.key === stepKey) // 映射不到 → -1
  return {
    available: true,
    steps: TRIAL_STEPS,
    activeIndex,
    stateLabel: TRIAL_STATE_LABEL[trial.state] ?? null,
    anomaly: trial.anomaly,
    since: trial.since,
    until: trial.until ?? null,
    level: trial.anomaly ? 'anomaly' : 'normal',
  }
}

// ---------- 爆炸半径角标（blast_radius 是自由文本，关键词映射 + 原文兜底） ----------
export type BlastLevel = 'low' | 'medium' | 'high' | 'critical' | 'unknown'

export interface BlastMeta { raw: string; short: string; level: BlastLevel; tagType: TagType }

export function blastMeta (radius: string): BlastMeta {
  const raw = (radius ?? '').trim()
  const s = raw.toLowerCase()
  const hit = (words: string[]) => words.some(w => s.includes(w))
  if (hit(['critical', '严重', '致命'])) return { raw, short: '严重', level: 'critical', tagType: 'error' }
  if (hit(['high', '高']))             return { raw, short: '高',   level: 'high',     tagType: 'error' }
  if (hit(['medium', '中']))           return { raw, short: '中',   level: 'medium',   tagType: 'warning' }
  if (hit(['low', '低', '小']))        return { raw, short: '低',   level: 'low',      tagType: 'default' }
  return { raw, short: raw ? '?' : '未知', level: 'unknown', tagType: 'default' }
}

// ---------- 分组 / 排序 ----------
export interface AgentGroup { agent_id: string; agent_name: string; items: Automation[] }

//: 两个哨兵都早于 Date 能表示的最小时刻（-8.64e15）⇒ 一起沉底，且坏时间戳比"从未触发"再靠后
//: 一档：缺席是常态，读不出是数据出问题了；混在同一档会按名称插进正常项中间，伪装成"很久没触发"。
const NEVER = -8.64e15 - 1
const BROKEN = -8.64e15 - 2

function triggerKey (raw: string | null): number {
  if (!raw) return NEVER
  const ts = Date.parse(raw)
  return Number.isNaN(ts) ? BROKEN : ts
}

export function compareAutomation (a: Automation, b: Automation): number {
  const ka = triggerKey(a.last_triggered)
  const kb = triggerKey(b.last_triggered)
  if (ka !== kb) return kb - ka                              // 最近触发的排前面
  return collator.compare(a.name, b.name) || collator.compare(a.id, b.id)
}

export function groupByAgent (items: Automation[]): AgentGroup[] {
  const map = new Map<string, AgentGroup>()
  for (const item of items) {
    const group = map.get(item.agent_id) ?? { agent_id: item.agent_id, agent_name: item.agent_name, items: [] }
    group.items.push(item)
    map.set(item.agent_id, group)
  }
  const groups = [...map.values()]
  for (const g of groups) g.items.sort(compareAutomation)
  groups.sort((a, b) => collator.compare(a.agent_name, b.agent_name) || collator.compare(a.agent_id, b.agent_id))
  return groups
}

export interface Split { active: Automation[]; archived: Automation[] }

/** 归档项进归档列表；status==='pending' 的草稿由待批块接管，不进启用列表 */
export function splitByArchived (items: Automation[]): Split {
  const active: Automation[] = []
  const archived: Automation[] = []
  for (const it of items) {
    if (it.archived) archived.push(it)
    else if (it.status !== 'pending') active.push(it)
  }
  return { active, archived }
}

export function deviceLabel (d: DeviceRef): string {
  return (d && (d.friendly_name || d.entity_id)) || '未知设备'
}