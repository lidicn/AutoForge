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
  anomaly: string | null
  since: string | null
  level: 'normal' | 'anomaly'
}

export function trialMeta (trial: TrialInfo | null): TrialMeta {
  if (!trial) {
    return { available: false, steps: TRIAL_STEPS, activeIndex: -1, anomaly: null, since: null, level: 'normal' }
  }
  const activeIndex = TRIAL_STEPS.findIndex(s => s.key === trial.state) // 未知状态 → -1
  return {
    available: true,
    steps: TRIAL_STEPS,
    activeIndex,
    anomaly: trial.anomaly,
    since: trial.since,
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

export function compareAutomation (a: Automation, b: Automation): number {
  const ta = a.last_triggered ? Date.parse(a.last_triggered) : NaN
  const tb = b.last_triggered ? Date.parse(b.last_triggered) : NaN
  const va = Number.isNaN(ta) ? -Infinity : ta
  const vb = Number.isNaN(tb) ? -Infinity : tb
  if (va !== vb) return vb - va                              // 最近触发的排前面
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