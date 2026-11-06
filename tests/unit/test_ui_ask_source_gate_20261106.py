"""判据 · v1.28 R4：asks 视图的应答来源标签与已应答历史（2026-11-06，AF-UI）。

为什么钉：
1. `answered_via` 是后端新增的**可选**字段（枚举 `xiaomi`/`webui`）。前端最容易犯的错不是渲染错，
   而是「为了把 UI 凑满」给缺失值补一枚「未知来源」标签——那是**伪造事实**，读的人会当结论用。
   所以本族钉死：标签只挂在已知字段值上，缺失/`null` 一律不渲染。
2. 已应答历史（`answered[]`）有两种「空」：字段缺失（这份读数没带历史）与空数组（最近确实没有应答）。
   文案必须分开，否则「没读到」会被读成「没有」——v1.26 撤回清单同族教训。

四腿：
A 类型面：`AskItem` 带 `answered_via`、`AsksResponse` 带 `answered`、`AskAnswerHistoryItem` 在册；
B 在途卡只有两枚来源标签，且分别挂 `=== 'xiaomi'` / `=== 'webui'`（⛔ 无兜底标签）；
C 「最近已应答」卡在、写明「最多 5 条」；
D 缺字段与空数组分开：`answeredFieldSeen` 守卫 ＋ 「不等于没有应答过」边界句。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_UI_SRC = ROOT / "ui" / "src"


def _view() -> str:
    return (_UI_SRC / "views" / "AsksView.vue").read_text(encoding="utf-8")


def test_types_carry_answer_source_and_history():
    types = (_UI_SRC / "types" / "api.ts").read_text(encoding="utf-8")
    item = types.split("export interface AskItem {", 1)[1].split("}", 1)[0]
    assert "answered_via?" in item, "AskItem 缺 answered_via ⇒ 后端给了也传不出界面"
    resp = types.split("export interface AsksResponse {", 1)[1].split("}", 1)[0]
    assert "answered?" in resp, "AsksResponse 缺 answered[] ⇒ 历史面拿不到数据"
    assert "export interface AskAnswerHistoryItem {" in types
    hist = types.split("export interface AskAnswerHistoryItem {", 1)[1].split("}", 1)[0]
    for field in ("answered_via?", "answered_at?"):
        assert field in hist, f"AskAnswerHistoryItem 缺 {field}"


def test_pending_cards_only_tag_known_sources():
    src = _view()
    assert "经小爱应答" in src and "WebUI 应答" in src
    assert "a.answered_via === 'xiaomi'" in src
    assert "a.answered_via === 'webui'" in src
    # ⛔ 不许给缺失值兜底一枚标签：那会把「没带来源」渲染成有结论
    assert "未知来源" not in src


def test_history_card_is_present_and_bounded():
    src = _view()
    assert 'title="最近已应答"' in src, "asks 视图没有「最近已应答」历史面"
    assert "最多 5 条" in src, "历史面没写条数上界（后端环形是 5 条）"


def test_missing_field_is_not_rendered_as_none_answered():
    src = _view()
    assert 'v-if="!answeredFieldSeen"' in src, "缺字段与空数组没分开：会把「没读到历史」显示成「没有应答过」"
    assert "不等于" in src and "没有应答过" in src, "缺字段的文案没写「不等于没有应答过」这句边界"
    assert 'description="最近没有已应答记录"' in src, "空数组的空态文案没落"
