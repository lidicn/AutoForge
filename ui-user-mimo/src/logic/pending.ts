/**
 * 待批列表行归一（AF-726）——**纯函数**，不碰 fetch / localStorage，判据可钉真行为。
 *
 * 后端 `/api/pending/list` 每行已带 `l2_summary`（`af_service.list_pending` 机提取，
 * DCD 裁定 20261031·矩阵行 2）。本函数只**透传**，⛔ 前端不算 L2。
 * ★ 三态必须可区分（家规同管理员面 `ui/PendingView.vue`：`Array.isArray(l2) ? l2 : null`）：
 *   - 数组且非空 ⇒ 本条含这些 L2 动作；
 *   - 数组且为空 ⇒ 本条不含 L2 动作；
 *   - **缺字段／不是数组** ⇒ **`null`＝「未读到」**（后端旧版时正是必须说「不知道」的时刻，
 *     ⛔ 绝不能归成 `[]`，那会把「没读到」说成「没有」＝假保证）。
 */
import type { L2Action, PendingItem } from '../types/api.ts'

function isRecord (v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

function str (v: unknown): string {
  return v == null ? '' : String(v)
}

/** 后端 L2 清单归一：数组 ⇒ 映射（空数组保持空）；非数组（缺字段/坏形状）⇒ `null`＝未读到。 */
export function normalizeL2Summary (raw: unknown): L2Action[] | null {
  if (!Array.isArray(raw)) return null
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