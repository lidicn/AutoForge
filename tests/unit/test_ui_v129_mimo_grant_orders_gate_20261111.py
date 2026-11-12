"""判据 · v1.29 UI 批：mimo 授权管理页＋任务单回执面（2026-11-11，AF-UI）。

为什么钉这几格（都是**会骗人**的写法）：
1. **撤销/取消必须二次确认**——两者都立即生效（撤销＝DCD §2.3；取消＝终态即销），
   误触直撤没有任何撤销入口。判据钉 `n-popconfirm` 在场。
2. **读失败 ≠ 空列表**——列表/留痕两处都要「保留上次读数并如实标注」；把故障清成空表，
   读的人会以为「本来就没有授权/没有回执」（v1.26 撤回清单同族教训）。
3. **凭据红线⑤**：授权页只管管理，⛔ 不出现任何码值输入/回显（签发不在本批）；页面不打印 token。
4. **两个 nav 入口＋两条路由**要在册：进不去的页面＝没做。

⛔ 本判据只钉「字面/形状在且挂对条件」，渲染效果由真机/浏览器取证负责。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIMO = ROOT / "ui-user-mimo" / "src"


def _read(rel: str) -> str:
    return (_MIMO / rel).read_text(encoding="utf-8")


def test_routes_and_nav_entries_exist():
    router = _read("router.ts")
    assert "GrantsView.vue" in router and "OrdersView.vue" in router
    # v1.29 第三批：场景页（SceneDef）——进不去的页面＝没做
    assert "ScenesView.vue" in router, "场景页没挂路由"
    layout = _read("views/MainLayout.vue")
    assert "grants" in layout and "orders" in layout
    assert "'scenes'" in layout, "场景页没有 nav 入口"
    # v1.29 第三批：5→6 个 tab。⛔ 少一列会挤压/溢出（手机 375px 六格必量）
    assert "repeat(6, 1fr)" in layout, "6 个 tab 必须改成 6 列，否则挤压/溢出"


def test_revoke_and_cancel_require_confirmation():
    grants = _read("views/GrantsView.vue")
    assert "NPopconfirm" in grants, "撤销没有二次确认（撤销立即生效，误触无法回滚）"
    assert "撤销立即生效" in grants
    orders = _read("views/OrdersView.vue")
    assert "NPopconfirm" in orders, "取消没有二次确认（终态即销）"
    assert "终态即销" in orders


def test_read_failure_is_not_rendered_as_empty():
    grants = _read("views/GrantsView.vue")
    orders = _read("views/OrdersView.vue")
    scenes = _read("views/ScenesView.vue")      # v1.29 第三批：场景页同纪律
    for src, name in ((grants, "GrantsView"), (orders, "OrdersView"), (scenes, "ScenesView")):
        assert "读取失败" in src, f"{name} 没有读失败文案"
        assert "保留上次成功读数" in src, f"{name} 读失败时没保留上次读数（会被读成空列表）"
        assert "这不等于「没有" in src or "这不等于「没有" in src, f"{name} 缺「读失败≠没有」边界句"


def test_hidden_count_is_surfaced_on_both_pages():
    """群规⑩：非 owner 面必报 hidden_count；页面不给提示＝用户以为看全了。"""
    for rel in ("views/GrantsView.vue", "views/OrdersView.vue", "views/ScenesView.vue"):
        src = _read(rel)
        assert "hidden_count" in src and "条不在你名下" in src, rel


def test_scene_api_layer_has_the_five_calls():
    """v1.29 第三批：场景五枚调用点（列／存／取／删／跑）必须在 API 层，且写面 stringify。"""
    http = _read("api/http.ts")
    for call in ("listScenes", "saveScene", "getScene", "deleteScene", "runScene"):
        assert call in http, f"http.ts 缺 {call}"
    assert "'/api/scenes'" in http
    assert "/api/scenes/${encodeURIComponent(sceneId)}/run" in http, "run 路径必须带编码后的 scene_id"
    assert "JSON.stringify({ scene, replace })" in http, "存定义 body 必须 stringify"
    assert "JSON.stringify({ phrase, agent, grant_id: grantId })" in http, "run body 必须 stringify 且键名 grant_id"


def test_no_credential_input_or_echo():
    """凭据红线⑤：本批⛔ 签发、⛔ 任何码值输入/回显（码值只活在授权码页）。"""
    for rel in ("views/GrantsView.vue", "views/OrdersView.vue"):
        src = _read(rel)
        assert "auth_code" not in src, f"{rel} 出现了码值字段（本批不做签发）"
        assert "Bearer" not in src, f"{rel} 里出现 Bearer（令牌不许进页面代码）"
        assert "localStorage" not in src, f"{rel} 不该碰存储（token 由 api 层管）"


def test_api_layer_has_the_six_calls():
    http = _read("api/http.ts")
    for call in ("listGrants", "revokeGrant", "listGrantReceipts", "listOrders", "listOrderReceipts", "cancelOrder"):
        assert call in http, f"http.ts 缺 {call}"
    assert "'/api/grants'" in http and "'/api/orders'" in http
    assert "/api/grants/receipts?limit=" in http and "/api/orders/receipts?limit=" in http
    # 写面 body 必须 stringify（mimo 既有判据同族：fetch 发对象会变 "[object Object]"）
    assert "JSON.stringify({ reason: reason || '' })" in http


def test_logic_module_is_pure_and_pinned():
    """状态派生只在前端逻辑层做「展示」：撤销优先于到期、缺有效期不当作永久。"""
    logic = _read("logic/grants.ts")
    assert "export function grantState" in logic
    assert "revoked" in logic and "expired" in logic
    assert "export function groupOrderReceipts" in logic
    assert "export function hiddenCountText" in logic
