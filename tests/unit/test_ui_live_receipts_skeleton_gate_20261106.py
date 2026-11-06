"""判据 · v1.28 R5 骨架位：真机下发页「最近单发回执」在 R3 端点未就绪时**必须明写未接线**（2026-11-06，AF-UI）。

为什么要钉这一格：缺数据面的卡有两种写法——① 留空 / 写「暂无记录」，② 明写「未接线（待 R3 端点）」。
①看着更干净，但它是一句**假读数**：下一个人（或下一任席位）会把「空」读成后端结论「没有回执」，
于是要么以为功能已坏、要么以为本来就没下发过。v1.26 的「撤销清单读不到 ≠ 窗口内没有可撤销项」
踩过同族坑，这里的骨架位同理，所以把「怎么说」也当判据钉住。

三腿：
A 卡片在、标题是「最近单发回执」（位置在「下发结果」与「可撤销的部署」之间）；
B 明写「未接线（待 R3 端点）」＋一句「不代表没有回执」的边界说明；
C ⛔ 不猜端点名：R3 路径未获 PM 核准前，源码里不许出现任何 receipts 路径字面量、也不许先摆假表格。
   （Phase 2 真接上时，C 这条随该批一起改——它是棘轮，不是永久禁令。）
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE = ROOT / "ui" / "src" / "views" / "LiveView.vue"


def _src() -> str:
    return LIVE.read_text(encoding="utf-8")


def test_receipts_card_exists_and_is_named():
    src = _src()
    assert 'title="最近单发回执"' in src, "真机下发页没有「最近单发回执」卡"


def test_skeleton_says_unwired_not_empty():
    """「未接线」必须**写在卡面上**，不能只活在提交信息或报告里。"""
    src = _src()
    assert "未接线（待 R3 端点）" in src, "骨架位没明写未接线（待 R3 端点）"
    assert "接口未就绪（待 R3 端点）" in src, "空态文案没写接口未就绪"
    assert "不代表没有回执" in src, "没写「未接线 ≠ 没有回执」这句边界——它正是这一格的用途"


def test_no_guessed_endpoint_and_no_fake_table():
    """R3 路径未核准 ⇒ 不许先猜端点名；骨架位也不许先摆一张空表格冒充数据面。"""
    src = _src()
    assert "receipts" not in src, "R3 路径尚未核准，骨架位⛔ 不许先猜端点名"
    assert "<n-table" not in src, "骨架位⛔ 不许先摆假表格（空表会被读成「后端说没有回执」）"
