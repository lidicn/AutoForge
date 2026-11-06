"""判据 · v1.28 R5 Phase 2：真机下发页「最近单发回执」接 R3 端点后的渲染口径（2026-11-07，AF-UI）。

R3 契约（AF-Core 定死、PM 核准）：`GET /api/live/run/receipts?limit=20`
→ 行 `{at, graph[], actions, verdict, code_mask}`；非 owner 面 `hidden_count` 在**顶层**；
后端今天只有**两档状态**：`ok:false`（可带 `read_error`）／`ok:true`（items 可能为空）。

四腿：
A 接线只走 api 门面：client.ts 有端点字面量、index.ts facade 透传、视图调 `facade.liveReceipts()`；
B 前端两档渲染（后端三档）：`ok:false` ⇒ 异常条（`read_error` 原样，覆盖 no_store／ledger_unreadable）；
   `ok:true` ⇒ 表格／空表。⛔ 不存在「读失败→保留上次读数标黄」的假档（读失败已如实 ok:false）；
C 空表话术不替后端断言不存在：写「没有读到回执记录」＋「⛔ 不等于『从未下发』」＋「空表只代表台账里没有事件」
  （读失败走 ok=false，不会混成空表）；
D 三种「原样」：`verdict` 原样（⛔ 无中文译名映射）、`code_mask` 原样（⛔ 前端不拼码）、
   `graph[]` 单元素是常态（>3 折叠「等 N 个」）、`hidden_count` 顶层提示。

**接续说明（判据退休留名）**：Phase 1 的骨架判据 `test_ui_live_receipts_skeleton_gate_20261106.py`
在本批**整枚退休**——它的 A/B 腿钉的是「卡面明写未接线（待 R3 端点）」这类文案，而骨架卡在本批被
真接线表取代，旧文案不复存在；继续留它就是拿已消失的字符串做假绿。它原来的 C 腿（⛔ 不猜端点名）
是**棘轮**，本批按它自己的约定翻转为「必须真接端点」（见本文件 A 腿）。三段意图（未接线不许说成
没回执 → 空表不许说成没有 → 掩码/结论不许自造）全部由本文件 B/C/D 腿承接，不是放弃判据。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_UI_SRC = ROOT / "ui" / "src"


def _view() -> str:
    return (_UI_SRC / "views" / "LiveView.vue").read_text(encoding="utf-8")


# ── A 接线（api 门面唯一真源）────────────────────────────────────────
def test_endpoint_is_wired_through_the_api_layer():
    client = (_UI_SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert "'/live/run/receipts?limit=20'" in client, "client.ts 没接 R3 端点"
    assert "liveReceipts:" in client
    index = (_UI_SRC / "api" / "index.ts").read_text(encoding="utf-8")
    assert "liveReceipts: () => api.liveReceipts()" in index, "facade 没透传 liveReceipts"
    assert "facade.liveReceipts()" in _view(), "视图没走 facade（自己拼 URL 会绕过契约门）"


def test_types_carry_receipt_shape():
    types = (_UI_SRC / "types" / "api.ts").read_text(encoding="utf-8")
    item = types.split("export interface LiveReceiptItem {", 1)[1].split("}", 1)[0]
    for f in ("at", "graph", "actions", "verdict", "code_mask"):
        assert f in item, f"LiveReceiptItem 缺 {f}"
    resp = types.split("export interface LiveReceiptsResponse {", 1)[1].split("}", 1)[0]
    for f in ("ok", "items?", "hidden_count?", "read_error?"):
        assert f in resp, f"LiveReceiptsResponse 缺 {f}"


# ── B 两档渲染（后端三档：文件缺⇒ok:true 空表／no_store⇒ok:false／ledger_unreadable⇒ok:false）──
def test_two_state_rendering_and_no_fake_read_failure_branch():
    src = _view()
    assert "receipts.ok === false" in src, "没有 ok:false 这一档（服务端读数失败会被显示成空表）"
    assert "receipts.read_error" in src, "ok:false 时没把 read_error 原样显示出来"
    assert "receiptsError" in src, "传输层失败没有单独一档（会被当成空表）"
    # ⛔ 假档反证：只允许**传输层**异常保留上次读数；后端读失败已如实 ok:false，UI 不再自造一档
    assert "保留上次成功读数" in src
    assert "没有 LIVE_RUN_EXECUTED" in src, "空表脚注要按三档事实写（空表只代表台账里没有事件）"


# ── C 空表话术 ──────────────────────────────────────────────────────
def test_empty_table_does_not_assert_absence():
    src = _view()
    assert 'description="没有读到回执记录"' in src, "空表文案不该写「没有回执」"
    assert "从未下发" in src, "空表缺「≠从未下发」的边界脚注"


# ── D 原样渲染 ──────────────────────────────────────────────────────
def test_verdict_and_mask_are_rendered_verbatim():
    src = _view()
    assert "{{ r.verdict }}" in src, "verdict 不是原样渲染"
    assert "verdict:" not in src, "视图里出现了 verdict 译名映射（⛔ 前端不自造中文译名）"
    assert "{{ r.code_mask }}" in src, "code_mask 不是原样渲染"
    # 前端不许自己拼码：视图里不出现 `+ '***'` / slice 掩码拼装
    assert "***" not in src, "视图里在拼掩码（⛔ 掩码由后端给）"


def test_graph_array_folds_and_hidden_count_is_surfaced():
    src = _view()
    assert "slice(0, 3)" in src and "等 " in src, "graph[] 超 3 个没折叠"
    assert "graphText(r.graph)" in src
    assert "hidden_count" in src and "条不在你名下" in src, "非 owner 的 hidden_count 没提示"
