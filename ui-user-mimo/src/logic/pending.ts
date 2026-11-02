/**
 * 待批列表行归一（AF-726）——**纯函数**，不碰 fetch / localStorage，判据可钉真行为。
 *
 * 后端 `/api/pending/list` 每行已带 `l2_summary`（`af_service.list_pending` 机提取，
 * DCD 裁定 20261031·矩阵行 2）。本函数只**透传**，⛔ 前端不算 L2。
 * ★ 缺字段 ⇒ `[]`（「本条不含 L2 动作」），⛔ 不是 `undefined`（「没读出来」）——
 *   两者必须可区分，与回执侧 `l2summary.test.mjs` 同口径。
 */
import type { L2Action, PendingItem } from '../types/api.ts'

function isRecord (v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

function str (v: unknown): string {
  return v == null ? '' : String(v)
}

/** 后端 L2 清单归一：非数组（缺字段/坏形状）⇒ 空数组。 */
export function normalizeL2Summary (raw: unknown): L2Action[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((o) => ({
    action: str(o.action),
    alias: str(o.alias),
    automation: str(o.automation),
    node: str(o.node),
  }))
}

/** 待批行映射（唯一映射点）：后端原始行 → `PendingItem`。 */
export function toPendingItem (
  raw: Record<string, any>,
  toIso: (v: unknown) => string | null,
): PendingItem {
  return {
    op_id: str(raw.op_id),
    summary: str(raw.summary),
    agent_name: str(raw.agent),
    submitted_at: typeof raw.submitted_at === 'string' ? raw.submitted_at : (toIso(raw.submitted_at) ?? ''),
    blast_radius: isRecord(raw.blast_radius) ? JSON.stringify(raw.blast_radius) : str(raw.blast_radius),
    l2_summary: normalizeL2Summary(raw.l2_summary),
  }
}