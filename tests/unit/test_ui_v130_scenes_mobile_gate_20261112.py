"""判据 · v1.30 批 2 移动端四腿（2026-11-12，AF-UI；PM 裁定补判据腿，off `51a3f50`）。

背景：v1.30 场景面（`4d02839` 交付，pin 收尾在 `51a3f50`）已在 `origin/master` 且 CI 绿，
但那条线**没有移动端约束的判据腿**——出资人 2026-11-12 明确要求「页面可整体缩放、放大后能拖到
四角、上下栏不挡内容」。本文件只补四条**读源码即可判**的腿，⛔ 不混其它改动、⛔ 不动 `src/`：

1. topbar/tabbar 由 `fixed` 改 `sticky`（抖动才回退 relative，回退必须写进回执——所以这里只钉
   当前既定形态；若将来回退 relative，本腿要同步改并说明，⛔ 不许静默换回 fixed）；
2. 全局 `touch-action: pan-x pan-y pinch-zoom`（缩放交给浏览器原生，⛔ 自写手势引擎）；
3. viewport 保持可缩放：⛔ 无 `maximum-scale`／`user-scalable=no`（出资人定，禁缩放＝禁平移前提）；
4. 6 个 tab 不溢出：`repeat(6, 1fr)`＋`.tabbar-inner` 横向溢出兜底。

说明：**渲染效果**（200% 缩放四角可达、sticky 不抖）不在本判据射程，由部署面/真机取证负责。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIMO = ROOT / "ui-user-mimo"


def _read(rel: str) -> str:
    return (_MIMO / rel).read_text(encoding="utf-8")


def test_topbar_and_tabbar_are_sticky_not_fixed():
    layout = _read("src/views/MainLayout.vue")
    assert "position: sticky; top: 0" in layout, "topbar 没改成 sticky（top: 0）"
    assert "position: sticky; bottom: 0" in layout, "tabbar 没改成 sticky（bottom: 0）"
    assert "position: fixed" not in layout, \
        "MainLayout 里还有 fixed 栏：本批既定形态是 sticky；若因抖动回退 relative，须改本判据并在回执写明"


def test_global_touch_action_allows_native_pinch_zoom():
    css = _read("src/styles/main.css")
    assert "touch-action: pan-x pan-y pinch-zoom" in css, "全局 touch-action 未声明（放大后无法平移/缩放）"
    assert "overscroll-behavior: none" not in css, "⛔ 不许加 overscroll-behavior:none（会吃掉整屏平移）"


def test_viewport_stays_scalable():
    html = _read("index.html")
    m = re.search(r'name="viewport"[^>]*content="([^"]+)"', html)
    assert m, "index.html 找不到 viewport"
    content = m.group(1)
    assert "maximum-scale" not in content, "viewport 出现 maximum-scale（禁缩放）"
    assert "user-scalable" not in content, "viewport 出现 user-scalable（禁缩放）"
    assert "width=device-width" in content and "initial-scale=1" in content, "viewport 基础项缺失"


def test_six_tabs_fit_with_overflow_fallback():
    layout = _read("src/views/MainLayout.vue")
    assert "repeat(6, 1fr)" in layout, "6 个 tab 必须 6 等分列"
    # 390px 宽下六格 label 仍可能挤压 ⇒ tabbar-inner 必须有横向溢出兜底，不许裁切
    tabbar_inner = layout.split(".tabbar-inner", 1)[-1].split("}", 1)[0]
    assert "overflow-x: auto" in tabbar_inner, ".tabbar-inner 缺 overflow-x:auto 兜底（窄屏六格会被裁切）"
